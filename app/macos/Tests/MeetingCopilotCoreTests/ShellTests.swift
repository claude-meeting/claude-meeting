import XCTest
@testable import MeetingCopilotCore

/// Collects streamed lines from any thread.
private final class Lines: @unchecked Sendable {
    private let lock = NSLock()
    private var items: [String] = []
    func append(_ s: String) { lock.lock(); items.append(s); lock.unlock() }
    var all: [String] { lock.lock(); defer { lock.unlock() }; return items }
}

final class ShellTests: XCTestCase {

    func testRunStatusSeparatesStdoutStderrAndExitCode() async {
        let r = await Shell.runStatus("/bin/sh", ["-c", "echo out1; echo err1 1>&2; echo out2; exit 3"])
        XCTAssertEqual(r.out, "out1\nout2\n")
        XCTAssertEqual(r.err, "err1\n")
        XCTAssertEqual(r.code, 3)
    }

    func testRunReturnsStdoutOnly() async {
        let out = await Shell.run("/bin/sh", ["-c", "echo visible; echo hidden 1>&2"])
        XCTAssertEqual(out, "visible\n")
    }

    func testRunStatusPassesArgumentsVerbatim() async {
        let r = await Shell.runStatus("/bin/sh", ["-c", #"printf '%s|' "$@""#, "sh", "a b", "", "c#d"])
        XCTAssertEqual(r.out, "a b||c#d|")
        XCTAssertEqual(r.code, 0)
    }

    func testRunStatusTimeoutReturns() async {
        let start = Date()
        let r = await Shell.runStatus("/bin/sh", ["-c", "exec sleep 20"], timeout: 0.5)
        let elapsed = Date().timeIntervalSince(start)
        XCTAssertLessThan(elapsed, 10, "the watchdog must end the process")
        XCTAssertNotEqual(r.code, 0, "a killed process does not report success")
        XCTAssertEqual(r.out, "")
    }

    func testRunStatusMissingToolReportsFailure() async {
        let r = await Shell.runStatus("/nonexistent/tool-\(UUID().uuidString)", [])
        XCTAssertEqual(r.code, -1)
        XCTAssertEqual(r.out, "")
        XCTAssertFalse(r.err.isEmpty)
    }

    func testEnvironmentPutsLocalBinFirstOnPath() {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let path = Shell.environment["PATH"] ?? ""
        XCTAssertTrue(path.hasPrefix("\(home)/.local/bin:"), path)
        XCTAssertTrue(path.contains(":/usr/bin:"), path)
    }

    func testStreamDeliversFinalLineWithoutTrailingNewline() async {
        let lines = Lines()
        let ok = await Shell.stream("/bin/sh", ["-c", "printf 'first\\nsecond\\nlast-no-newline'"]) { lines.append($0) }
        XCTAssertTrue(ok)
        XCTAssertEqual(lines.all, ["first", "second", "last-no-newline"])
    }

    func testStreamMergesStderrAndReportsExitStatus() async {
        let lines = Lines()
        let ok = await Shell.stream("/bin/sh", ["-c", "echo out; echo err 1>&2; exit 2"]) { lines.append($0) }
        XCTAssertFalse(ok)
        XCTAssertEqual(Set(lines.all), ["out", "err"])
        XCTAssertEqual(lines.all.count, 2)
    }

    func testStreamWithNoOutputDeliversNothing() async {
        let lines = Lines()
        let ok = await Shell.stream("/bin/sh", ["-c", "exit 0"]) { lines.append($0) }
        XCTAssertTrue(ok)
        XCTAssertEqual(lines.all, [])
    }

    func testInputGoesToTheToolsStdin() async {
        // The model service's key is handed over this way, never among the arguments.
        let r = await Shell.runStatus("/bin/cat", [], input: "sk-test\n第二行")
        XCTAssertEqual(r.out, "sk-test\n第二行")
        XCTAssertEqual(r.code, 0)
    }
}
