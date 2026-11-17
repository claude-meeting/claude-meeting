import XCTest
@testable import MeetingCopilotCore

final class AppNotificationsTests: XCTestCase {
    private var dir: URL!

    override func setUpWithError() throws {
        dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try FileManager.default.removeItem(at: dir)
    }

    private func write(_ name: String, _ text: String) throws {
        try text.write(to: dir.appendingPathComponent(name), atomically: true, encoding: .utf8)
    }

    private func left() throws -> [String] {
        try FileManager.default.contentsOfDirectory(atPath: dir.path).sorted()
    }

    func testOldestFirstAndRemovedOnceRead() throws {
        try write("2000-b.json", #"{"title": "二", "text": "t", "sound": "Ping", "open": ""}"#)
        try write("1000-a.json", #"{"title": "一", "text": "t", "sound": "Glass", "open": "/m/a.md"}"#)
        let taken = AppNotifications.take(from: dir)
        XCTAssertEqual(taken.map(\.title), ["一", "二"])
        XCTAssertEqual(taken[0].open, "/m/a.md")
        XCTAssertEqual(try left(), [])
        XCTAssertEqual(AppNotifications.take(from: dir), [])
    }

    func testBrokenFilesGoAndFilesBeingWrittenStay() throws {
        try write("1000-a.json", "{not json")
        try write(".2000-b.json", "{half")
        try write("note.txt", "x")
        XCTAssertEqual(AppNotifications.take(from: dir), [])
        XCTAssertEqual(try left(), [".2000-b.json", "note.txt"])
    }

    func testTooOldToShow() throws {
        try write("1000-a.json", #"{"title": "t", "text": "t", "sound": "", "open": ""}"#)
        let later = Date().addingTimeInterval(AppNotifications.maxAge + 5)
        XCTAssertEqual(AppNotifications.take(from: dir, now: later), [])
        XCTAssertEqual(try left(), [])
    }

    func testNoFolder() {
        XCTAssertEqual(AppNotifications.take(from: dir.appendingPathComponent("missing")), [])
    }
}
