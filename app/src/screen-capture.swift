// Full-resolution screen capture for the meeting copilot.
//
// Captures every display (or only the main one, or one app's largest window) once per
// interval with ScreenCaptureKit, compares a perceptual hash (dHash) and a 160x90 grayscale
// thumbnail with the last saved frame of the same display, and writes only changed frames as JPEG:
//   <out>/display-<n>/frame-<unix-ms>.jpg     n = 1 for the main display, then the others
//   <out>/app/frame-<unix-ms>.jpg             with --app
// Runs until it receives SIGTERM/SIGINT or the stop file appears.
//
// Usage: screen-capture <out-dir> [--interval 1.0] [--displays all|main] [--app <bundle-id>] [--stop-file <path>]
//        screen-capture --request-access   ask macOS for Screen Recording permission and exit
//        screen-capture --access-status <file> [--request]   write granted/denied to <file> (and ask)
//        screen-capture --compare <a.jpg> <b.jpg>   print "same", "small change" or "changed" (for tests)
//
// Built into MeetingCopilotScreen.app (see install.sh) so that macOS lists it by name under
// Screen & System Audio Recording; a bare command-line binary often can't be added there.
import AppKit
import CoreGraphics
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

struct Options {
    var outDir = URL(fileURLWithPath: ".")
    var interval = 1.0
    var app: String? = nil
    var allDisplays = true
    var stopFile: String? = nil
    var hashDistanceSame = 4
    var smallChangeGap = 3.0      // seconds between frames saved for a small change only
}

func parseArgs() -> Options {
    var o = Options()
    var args = Array(CommandLine.arguments.dropFirst())
    guard !args.isEmpty else {
        FileHandle.standardError.write("usage: screen-capture <out-dir> [--interval s] [--displays all|main] [--app bundle-id] [--stop-file path]\n".data(using: .utf8)!)
        exit(2)
    }
    o.outDir = URL(fileURLWithPath: args.removeFirst())
    while !args.isEmpty {
        let flag = args.removeFirst()
        guard !args.isEmpty else { break }
        let value = args.removeFirst()
        switch flag {
        case "--interval": o.interval = max(0.2, Double(value) ?? 1.0)
        case "--app": o.app = value
        case "--displays": o.allDisplays = value != "main"
        case "--stop-file": o.stopFile = value
        default: break
        }
    }
    return o
}

func log(_ msg: String) {
    let ts = ISO8601DateFormatter().string(from: Date())
    FileHandle.standardError.write("\(ts) \(msg)\n".data(using: .utf8)!)
}

/// 64-bit difference hash of a 9x8 grayscale thumbnail.
func dhash(_ image: CGImage) -> UInt64 {
    let w = 9, h = 8
    var px = [UInt8](repeating: 0, count: w * h)
    px.withUnsafeMutableBytes { buf in            // the context must only write while the pointer is valid
        let ctx = CGContext(data: buf.baseAddress, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w,
                            space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue)!
        ctx.interpolationQuality = .medium
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
    }
    var bits: UInt64 = 0
    for row in 0..<h {
        for col in 0..<(w - 1) {
            bits = (bits << 1) | (px[row * w + col] > px[row * w + col + 1] ? 1 : 0)
        }
    }
    return bits
}

/// Grayscale thumbnail for changes too small for the dHash. In Meet the border that moves to whoever
/// is talking changes no dHash bit, so a frame could go unsaved for minutes while others spoke, and
/// their lines were named after the tile lit before. At 160x90 that border move changes
/// 100-200 pixels by 25-40 levels.
let thumbW = 160, thumbH = 90
let thumbLevels = 20              // a pixel changed when it moved by more than this
let thumbPixels = 40              // a small change when at least this many pixels changed

func thumbnail(_ image: CGImage) -> [UInt8] {
    var px = [UInt8](repeating: 0, count: thumbW * thumbH)
    px.withUnsafeMutableBytes { buf in
        let ctx = CGContext(data: buf.baseAddress, width: thumbW, height: thumbH, bitsPerComponent: 8, bytesPerRow: thumbW,
                            space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue)!
        ctx.interpolationQuality = .medium
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: thumbW, height: thumbH))
    }
    return px
}

func smallChange(_ a: [UInt8], _ b: [UInt8]) -> Bool {
    var n = 0
    for i in 0..<min(a.count, b.count) where abs(Int(a[i]) - Int(b[i])) > thumbLevels {
        n += 1
        if n >= thumbPixels { return true }
    }
    return false
}

enum Change { case same, small, big }

func change(_ hash: UInt64, _ thumb: [UInt8], since lastHash: UInt64?, _ lastThumb: [UInt8]?, sameBits: Int) -> Change {
    guard let lastHash, let lastThumb else { return .big }
    if (hash ^ lastHash).nonzeroBitCount > sameBits { return .big }
    return smallChange(thumb, lastThumb) ? .small : .same
}

func writeJPEG(_ image: CGImage, to url: URL) -> Bool {
    guard let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.jpeg.identifier as CFString, 1, nil) else { return false }
    CGImageDestinationAddImage(dest, image, [kCGImageDestinationLossyCompressionQuality: 0.8] as CFDictionary)
    return CGImageDestinationFinalize(dest)
}

struct Target {
    let name: String          // output subfolder
    let filter: SCContentFilter
    let width: Int
    let height: Int
}

func makeTargets(_ o: Options) async throws -> [Target] {
    let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: o.app == nil)
    if let bundle = o.app {
        let windows = content.windows.filter { $0.owningApplication?.bundleIdentifier == bundle && $0.frame.width > 300 }
        if let win = windows.max(by: { $0.frame.width * $0.frame.height < $1.frame.width * $1.frame.height }) {
            let filter = SCContentFilter(desktopIndependentWindow: win)
            let scale = max(1, Int(filter.pointPixelScale.rounded()))      // the window's own display scale
            return [Target(name: "app", filter: filter,
                           width: Int(win.frame.width) * scale, height: Int(win.frame.height) * scale)]
        }
        log("no window for \(bundle); capturing the displays instead")
    }
    let mainID = CGMainDisplayID()
    // Main display first, so display-1 is always the main one.
    var displays = content.displays.sorted { ($0.displayID == mainID ? 0 : 1, $0.displayID) < ($1.displayID == mainID ? 0 : 1, $1.displayID) }
    if !o.allDisplays { displays = Array(displays.prefix(1)) }
    guard !displays.isEmpty else {
        throw NSError(domain: "screen-capture", code: 1, userInfo: [NSLocalizedDescriptionKey: "no display"])
    }
    return displays.enumerated().map { i, d in
        let mode = CGDisplayCopyDisplayMode(d.displayID)
        return Target(name: "display-\(i + 1)", filter: SCContentFilter(display: d, excludingWindows: []),
                      width: mode?.pixelWidth ?? d.width, height: mode?.pixelHeight ?? d.height)
    }
}

var running = true
signal(SIGTERM) { _ in running = false }
signal(SIGINT) { _ in running = false }

if CommandLine.arguments.dropFirst().first == "--access-status", CommandLine.arguments.count > 2 {
    // For the app's setup guide: launched with `open` so macOS checks this app's own permission;
    // writes "granted" or "denied" to the given file and, when denied, asks for it.
    let granted = CGPreflightScreenCaptureAccess()
    try? (granted ? "granted" : "denied").write(toFile: CommandLine.arguments[2], atomically: true, encoding: .utf8)
    if !granted && CommandLine.arguments.contains("--request") { _ = CGRequestScreenCaptureAccess() }
    exit(0)
}
if CommandLine.arguments.dropFirst().first == "--compare", CommandLine.arguments.count > 3 {
    func load(_ path: String) -> CGImage? {
        guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path).absoluteURL as CFURL, nil) else { return nil }
        return CGImageSourceCreateImageAtIndex(src, 0, nil)
    }
    guard let a = load(CommandLine.arguments[2]), let b = load(CommandLine.arguments[3]) else {
        FileHandle.standardError.write("cannot read the images\n".data(using: .utf8)!)
        exit(2)
    }
    switch change(dhash(b), thumbnail(b), since: dhash(a), thumbnail(a), sameBits: Options().hashDistanceSame) {
    case .same: print("same")
    case .small: print("small change")
    case .big: print("changed")
    }
    exit(0)
}
if CommandLine.arguments.dropFirst().first == "--request-access" {
    // Adds this app to the Screen Recording list (switched off) and shows the system prompt.
    if CGPreflightScreenCaptureAccess() { print("granted"); exit(0) }
    print(CGRequestScreenCaptureAccess() ? "granted" : "not granted yet: switch it on in System Settings")
    exit(0)
}

let opts = parseArgs()
if !CGPreflightScreenCaptureAccess() {
    _ = CGRequestScreenCaptureAccess()
    log("no Screen Recording permission yet (TCC); capture will fail until it is granted")
}
try? FileManager.default.createDirectory(at: opts.outDir, withIntermediateDirectories: true)

let done = DispatchSemaphore(value: 0)
Task {
    var lastHash: [String: UInt64] = [:]
    var lastThumb: [String: [UInt8]] = [:]
    var lastSaved: [String: Date] = [:]
    var saved = 0, skipped = 0
    var targetsAge = Date.distantPast
    var targets: [Target] = []
    var failures = 0
    while running {
        if let stop = opts.stopFile, FileManager.default.fileExists(atPath: stop) { break }
        let t0 = Date()
        do {
            if targets.isEmpty || Date().timeIntervalSince(targetsAge) > 10 {   // follow window moves, displays plugged in
                targets = try await makeTargets(opts)
                targetsAge = Date()
            }
            for target in targets {
                let config = SCStreamConfiguration()
                config.width = target.width
                config.height = target.height
                config.showsCursor = false
                let image = try await SCScreenshotManager.captureImage(contentFilter: target.filter, configuration: config)
                let h = dhash(image), thumb = thumbnail(image)
                let seen = change(h, thumb, since: lastHash[target.name], lastThumb[target.name], sameBits: opts.hashDistanceSame)
                // A small change waits a few seconds: a speaking avatar's ring moves every second.
                if seen == .same || (seen == .small && Date().timeIntervalSince(lastSaved[target.name] ?? .distantPast) < opts.smallChangeGap) {
                    skipped += 1
                    continue
                }
                let dir = opts.outDir.appendingPathComponent(target.name)
                try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
                let ms = Int(Date().timeIntervalSince1970 * 1000)
                let url = dir.appendingPathComponent("frame-\(ms).jpg")
                if writeJPEG(image, to: url) {
                    saved += 1
                    lastHash[target.name] = h
                    lastThumb[target.name] = thumb
                    lastSaved[target.name] = Date()
                    print(url.path)       // one line per saved frame, for anyone following stdout
                    fflush(stdout)
                }
            }
            failures = 0                                             // a clean pass: the back-off starts over
        } catch {
            failures += 1
            if failures <= 3 || failures % 20 == 0 {             // not a log line every few seconds all meeting
                log("capture failed (\(failures)x): \(error.localizedDescription)")
            }
            targets = []
            try? await Task.sleep(nanoseconds: UInt64(min(60, 3 * failures)) * 1_000_000_000)
        }
        let elapsed = Date().timeIntervalSince(t0)
        if elapsed < opts.interval {
            try? await Task.sleep(nanoseconds: UInt64((opts.interval - elapsed) * 1_000_000_000))
        }
    }
    log("stopped: saved \(saved) changed frames, skipped \(skipped) unchanged")
    done.signal()
}
done.wait()
