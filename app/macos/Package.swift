// swift-tools-version:6.0
// claude-meeting app: a menu bar icon and a Dock icon. Build the .app with ./build.sh.
//   MeetingCopilotCore      pure logic (notes parser, status/config decoding, strings, process helper)
//   MeetingCopilot          the SwiftUI/AppKit app; build.sh copies this executable into the bundle
//   MeetingCopilotCoreTests XCTest for the core: swift test
import PackageDescription

let package = Package(
    name: "MeetingCopilot",
    platforms: [.macOS(.v15)],
    targets: [
        .target(
            name: "MeetingCopilotCore",
            path: "Sources/MeetingCopilotCore",
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        .executableTarget(
            name: "MeetingCopilot",
            dependencies: ["MeetingCopilotCore"],
            path: "Sources/MeetingCopilot",
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        .testTarget(
            name: "MeetingCopilotCoreTests",
            dependencies: ["MeetingCopilotCore"],
            path: "Tests/MeetingCopilotCoreTests",
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
    ]
)
