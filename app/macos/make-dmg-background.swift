// Draw the disk image's window background (an arrow from the app to Applications) at 1x and 2x;
// build.sh passes it to dmgbuild with dmg-settings.py, and the same layout numbers to both.
// Usage: swift make-dmg-background.swift <out-dir> <width> <height> <app x> <Applications x> <icons y>
import AppKit

let args = CommandLine.arguments
guard args.count == 7, let nums = Optional(args[2...].compactMap { Double($0) }), nums.count == 5 else {
    FileHandle.standardError.write("usage: swift make-dmg-background.swift <out-dir> <width> <height> <app x> <Applications x> <icons y>\n".data(using: .utf8)!)
    exit(2)
}
let out = URL(fileURLWithPath: args[1])
try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)

let width = CGFloat(nums[0]), height = CGFloat(nums[1])
let appX = CGFloat(nums[2]), appsX = CGFloat(nums[3]), iconsY = CGFloat(nums[4])
func color(_ hex: Int, _ alpha: CGFloat = 1) -> NSColor {
    NSColor(calibratedRed: CGFloat((hex >> 16) & 255) / 255, green: CGFloat((hex >> 8) & 255) / 255,
            blue: CGFloat(hex & 255) / 255, alpha: alpha)
}

func draw(scale: CGFloat) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(width * scale), pixelsHigh: Int(height * scale),
                               bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    rep.size = NSSize(width: width, height: height)          // 144 dpi for the 2x image, as tiffutil expects
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    color(0xFBF7F0).setFill()
    NSRect(x: 0, y: 0, width: width, height: height).fill()

    // Finder places icons by their centre, from the top left; drawing starts at the bottom left.
    // The arrow spans the gap between the two 112-point icons, with a margin on each side.
    let y = height - iconsY, from = appX + 92, to = appsX - 90
    let arrow = NSBezierPath()
    arrow.lineWidth = 6; arrow.lineCapStyle = .round; arrow.lineJoinStyle = .round
    arrow.move(to: NSPoint(x: from, y: y))
    arrow.line(to: NSPoint(x: to - 2, y: y))
    arrow.move(to: NSPoint(x: to - 20, y: y + 16))
    arrow.line(to: NSPoint(x: to, y: y))
    arrow.line(to: NSPoint(x: to - 20, y: y - 16))
    color(0xD86E4F).setStroke()
    arrow.stroke()

    // English on every Mac, like the app's name and the "Applications" shortcut beside it.
    let caption = "Drag to Applications to install"
    let style = NSMutableParagraphStyle(); style.alignment = .center
    let attrs: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 14), .foregroundColor: color(0x6B5A52),
                                                 .paragraphStyle: style]
    caption.draw(in: NSRect(x: 0, y: 48, width: width, height: 24), withAttributes: attrs)
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

try! draw(scale: 1).write(to: out.appendingPathComponent("background.png"))
try! draw(scale: 2).write(to: out.appendingPathComponent("background@2x.png"))
