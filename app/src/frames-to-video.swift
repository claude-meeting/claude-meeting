// Turn a folder of changed screen frames into a review video with AVFoundation (no ffmpeg needed).
//
// Frames are named frame-<unix-ms>.jpg (bin/screen-capture); each is shown until the next one, so the
// video keeps the meeting's real timing. A gap longer than 10 minutes (capture paused) is shortened.
//
// Usage: frames-to-video <frames-dir> <out.mp4>
import AVFoundation
import CoreGraphics
import Foundation
import ImageIO

let maxHold = 600.0

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write("\(msg)\n".data(using: .utf8)!)
    exit(1)
}

let args = CommandLine.arguments
guard args.count == 3 else { fail("usage: frames-to-video <frames-dir> <out.mp4>") }
let dir = URL(fileURLWithPath: args[1])
let out = URL(fileURLWithPath: args[2])

let frames: [(ms: Int64, url: URL)] = ((try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)) ?? [])
    .compactMap { url in
        let name = url.lastPathComponent
        guard name.hasPrefix("frame-"), let dot = name.lastIndex(of: "."),
              let ms = Int64(name[name.index(name.startIndex, offsetBy: 6)..<dot]) else { return nil }
        return (ms, url)
    }
    .sorted { $0.ms < $1.ms }
guard frames.count >= 2 else { fail("\(dir.path): \(frames.count) frame(s), no video") }

func image(_ url: URL) -> CGImage? {
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
    return CGImageSourceCreateImageAtIndex(src, 0, nil)
}

guard let first = image(frames[0].url) else { fail("cannot read \(frames[0].url.path)") }
// H.264 wants even sizes, and encoders refuse 5K/6K frames: keep the longest edge at 3840.
let scale = min(1.0, 3840.0 / Double(max(first.width, first.height)))
let width = Int(Double(first.width) * scale) & ~1, height = Int(Double(first.height) * scale) & ~1

try? FileManager.default.createDirectory(at: out.deletingLastPathComponent(), withIntermediateDirectories: true)
try? FileManager.default.removeItem(at: out)
guard let writer = try? AVAssetWriter(outputURL: out, fileType: .mp4) else { fail("cannot write \(out.path)") }
let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
    AVVideoCodecKey: AVVideoCodecType.h264, AVVideoWidthKey: width, AVVideoHeightKey: height,
    AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: 1_500_000],
])
input.expectsMediaDataInRealTime = false
let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
    kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32ARGB,
    kCVPixelBufferWidthKey as String: width, kCVPixelBufferHeightKey as String: height,
])
guard writer.canAdd(input) else { fail("cannot add a video track") }
writer.add(input)
guard writer.startWriting() else { fail("cannot start writing: \(writer.error?.localizedDescription ?? "?")") }
writer.startSession(atSourceTime: .zero)

func buffer(_ img: CGImage) -> CVPixelBuffer? {
    guard let pool = adaptor.pixelBufferPool else { return nil }
    var pb: CVPixelBuffer?
    CVPixelBufferPoolCreatePixelBuffer(nil, pool, &pb)
    guard let pb else { return nil }
    CVPixelBufferLockBaseAddress(pb, [])
    defer { CVPixelBufferUnlockBaseAddress(pb, []) }
    guard let ctx = CGContext(data: CVPixelBufferGetBaseAddress(pb), width: width, height: height, bitsPerComponent: 8,
                              bytesPerRow: CVPixelBufferGetBytesPerRow(pb), space: CGColorSpaceCreateDeviceRGB(),
                              bitmapInfo: CGImageAlphaInfo.noneSkipFirst.rawValue) else { return nil }
    // Fit the frame inside the video (letterboxed) in case a display changed size mid-meeting.
    ctx.setFillColor(CGColor(gray: 0, alpha: 1))
    ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
    let fit = min(Double(width) / Double(img.width), Double(height) / Double(img.height))
    let w = Double(img.width) * fit, h = Double(img.height) * fit
    ctx.draw(img, in: CGRect(x: (Double(width) - w) / 2, y: (Double(height) - h) / 2, width: w, height: h))
    return pb
}

func waitReady() {
    while !input.isReadyForMoreMediaData {
        if writer.status != .writing { fail("writing failed: \(writer.error?.localizedDescription ?? "?")") }
        usleep(2000)
    }
}

var t = 0.0
var last: CVPixelBuffer?
for (i, frame) in frames.enumerated() {
    let next = i + 1 < frames.count ? Double(frames[i + 1].ms - frame.ms) / 1000 : 1
    autoreleasepool {
        if let img = image(frame.url), let pb = buffer(img) {      // an unreadable frame still takes its time
            waitReady()
            adaptor.append(pb, withPresentationTime: CMTime(seconds: t, preferredTimescale: 1000))
            last = pb
        }
    }
    t += min(max(next, 0.04), maxHold)
}
// Repeat the last frame at the end so it is shown for its full duration.
if let last {
    waitReady()
    adaptor.append(last, withPresentationTime: CMTime(seconds: t, preferredTimescale: 1000))
}
input.markAsFinished()
let done = DispatchSemaphore(value: 0)
writer.finishWriting { done.signal() }
done.wait()
guard writer.status == .completed else { fail("writing failed: \(writer.error?.localizedDescription ?? "?")") }
let size = (try? FileManager.default.attributesOfItem(atPath: out.path)[.size] as? Int) ?? 0
print("\(out.path): \(frames.count) frames, \(String(format: "%.1f", t / 60)) min, \(String(format: "%.1f", Double(size) / 1e6)) MB")
