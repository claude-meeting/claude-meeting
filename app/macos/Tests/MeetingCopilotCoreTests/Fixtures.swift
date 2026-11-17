import Foundation
import XCTest

/// The shared fixtures live beside the Python tests: app/tests/fixtures.
enum Fixtures {
    static let dir: URL = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()           // Tests/MeetingCopilotCoreTests
        .deletingLastPathComponent()           // Tests
        .deletingLastPathComponent()           // app/macos
        .deletingLastPathComponent()           // app
        .appendingPathComponent("tests/fixtures")

    static func text(_ name: String, file: StaticString = #filePath, line: UInt = #line) throws -> String {
        let url = dir.appendingPathComponent(name)
        return try XCTUnwrap(try? String(contentsOf: url, encoding: .utf8), "missing fixture \(url.path)",
                             file: file, line: line)
    }

    /// A fresh temporary directory, removed when the test ends.
    static func tempDir() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("MeetingCopilotCoreTests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }
}
