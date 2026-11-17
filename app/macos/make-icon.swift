// Draw the app icon (an ear with sound arcs, cream on terracotta) as an .iconset; build.sh turns it into
// AppIcon.icns with iconutil. Usage: swift make-icon.swift <out.iconset>
import AppKit

guard CommandLine.arguments.count > 1 else {
    FileHandle.standardError.write("usage: swift make-icon.swift <out.iconset>\n".data(using: .utf8)!)
    exit(2)
}
let out = URL(fileURLWithPath: CommandLine.arguments[1])
try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)

func color(_ hex: Int, _ alpha: CGFloat = 1) -> NSColor {
    NSColor(calibratedRed: CGFloat((hex >> 16) & 255) / 255, green: CGFloat((hex >> 8) & 255) / 255,
            blue: CGFloat(hex & 255) / 255, alpha: alpha)
}
let top = color(0xE3876A), bottom = color(0xBE5A3C), cream = color(0xFBF7F0)

func draw(_ px: Int) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8,
                               samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                               bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let s = CGFloat(px)
    let inset = s * 0.09                                    // macOS icon grid: art inside ~82 %
    let rect = NSRect(x: inset, y: inset, width: s - 2 * inset, height: s - 2 * inset)
    let shape = NSBezierPath(roundedRect: rect, xRadius: rect.width * 0.225, yRadius: rect.width * 0.225)
    NSGradient(starting: top, ending: bottom)!.draw(in: shape, angle: -70)

    // The ear: one thick curve for the rim and lobe, a shorter one inside.
    let cx = rect.minX + rect.width * 0.42, cy = rect.midY + rect.height * 0.04, r = rect.width * 0.24
    func p(_ x: CGFloat, _ y: CGFloat) -> NSPoint { NSPoint(x: cx + r * x, y: cy + r * y) }
    let rim = NSBezierPath()
    rim.lineWidth = rect.width * 0.085; rim.lineCapStyle = .round
    rim.move(to: p(0.15, -1.25))
    rim.curve(to: p(-0.95, 0.10), controlPoint1: p(-0.55, -1.30), controlPoint2: p(-1.00, -0.55))
    rim.curve(to: p(0.10, 1.05), controlPoint1: p(-0.90, 0.75), controlPoint2: p(-0.45, 1.08))
    rim.curve(to: p(0.85, 0.20), controlPoint1: p(0.60, 1.02), controlPoint2: p(0.88, 0.65))
    rim.curve(to: p(0.30, -0.55), controlPoint1: p(0.82, -0.15), controlPoint2: p(0.45, -0.30))
    let inner = NSBezierPath()
    inner.lineWidth = rect.width * 0.06; inner.lineCapStyle = .round
    inner.move(to: p(-0.35, -0.05))
    inner.curve(to: p(0.35, 0.25), controlPoint1: p(-0.35, 0.50), controlPoint2: p(0.25, 0.60))
    NSGraphicsContext.saveGraphicsState()
    let shadow = NSShadow()
    shadow.shadowColor = NSColor.black.withAlphaComponent(0.22)
    shadow.shadowBlurRadius = s * 0.025; shadow.shadowOffset = NSSize(width: 0, height: -s * 0.012)
    shadow.set()
    cream.setStroke(); rim.stroke(); inner.stroke()
    NSGraphicsContext.restoreGraphicsState()

    // Sound arriving from the right: two arcs, the far one fainter.
    for (k, alpha) in [(1.35, 0.9), (1.75, 0.55)] as [(CGFloat, CGFloat)] {
        let arc = NSBezierPath()
        arc.appendArc(withCenter: p(0.2, 0), radius: r * k, startAngle: -35, endAngle: 35)
        arc.lineWidth = rect.width * 0.05; arc.lineCapStyle = .round
        cream.withAlphaComponent(alpha).setStroke(); arc.stroke()
    }
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

for size in [16, 32, 128, 256, 512] {
    try! draw(size).write(to: out.appendingPathComponent("icon_\(size)x\(size).png"))
    try! draw(size * 2).write(to: out.appendingPathComponent("icon_\(size)x\(size)@2x.png"))
}
