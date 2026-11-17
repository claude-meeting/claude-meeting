import Foundation

/// Small process helpers: run and collect output, or stream it line by line.
public enum Shell {
    /// Apps started from the Finder get a bare PATH; tools installed by Homebrew, npm or Anthropic's
    /// installer live elsewhere. Same list as the background service's LaunchAgent.
    public static var environment: [String: String] {
        var env = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        env["PATH"] = "\(home)/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        return env
    }

    private static func make(_ tool: String, _ args: [String]) -> Process {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: tool)
        p.arguments = args
        p.environment = environment
        return p
    }

    /// stdout only (stderr is read separately so neither pipe can fill up and block the tool).
    public static func run(_ tool: String, _ args: [String], timeout: TimeInterval = 120) async -> String {
        await runStatus(tool, args, timeout: timeout).out
    }

    /// `input` is written to the tool's stdin (a secret goes there, never among the arguments, which other
    /// programs on the Mac can list).
    public static func runStatus(_ tool: String, _ args: [String], timeout: TimeInterval = 120, input: String? = nil)
        async -> (out: String, err: String, code: Int32) {
        await withCheckedContinuation { cont in
            DispatchQueue.global().async {
                let p = make(tool, args)
                let out = Pipe(), err = Pipe(), stdin = Pipe()
                p.standardOutput = out
                p.standardError = err
                if input != nil { p.standardInput = stdin }
                do { try p.run() } catch { cont.resume(returning: ("", "\(error)", -1)); return }
                if let input {
                    stdin.fileHandleForWriting.write(Data(input.utf8))
                    try? stdin.fileHandleForWriting.close()
                }
                let watchdog = DispatchWorkItem { if p.isRunning { p.terminate() } }
                DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: watchdog)
                var errData = Data()
                let errDone = DispatchSemaphore(value: 0)
                DispatchQueue.global().async {
                    errData = err.fileHandleForReading.readDataToEndOfFile()
                    errDone.signal()
                }
                let outData = out.fileHandleForReading.readDataToEndOfFile()
                p.waitUntilExit()
                errDone.wait()
                watchdog.cancel()
                cont.resume(returning: (String(data: outData, encoding: .utf8) ?? "",
                                        String(data: errData, encoding: .utf8) ?? "", p.terminationStatus))
            }
        }
    }

    public static func stream(_ tool: String, _ args: [String], line: @escaping @Sendable (String) -> Void) async -> Bool {
        await withCheckedContinuation { cont in
            DispatchQueue.global().async {
                let p = make(tool, args)
                let pipe = Pipe()
                p.standardOutput = pipe
                p.standardError = pipe
                do { try p.run() } catch { line("\(error)"); cont.resume(returning: false); return }
                var buffer = Data()
                while case let chunk = pipe.fileHandleForReading.availableData, !chunk.isEmpty {
                    buffer.append(chunk)
                    while let nl = buffer.firstIndex(of: 0x0A) {
                        line(String(data: buffer[..<nl], encoding: .utf8) ?? "")
                        buffer.removeSubrange(...nl)
                    }
                }
                if !buffer.isEmpty { line(String(data: buffer, encoding: .utf8) ?? "") }
                p.waitUntilExit()
                cont.resume(returning: p.terminationStatus == 0)
            }
        }
    }
}
