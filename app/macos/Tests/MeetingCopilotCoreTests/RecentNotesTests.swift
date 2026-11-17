import XCTest
@testable import MeetingCopilotCore

final class RecentNotesTests: XCTestCase {
    private var dir: URL!

    override func setUpWithError() throws {
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("recent-notes-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: dir)
    }

    private func note(_ name: String, minutesAgo: Double, bytes: Int = 400) throws -> URL {
        let url = dir.appendingPathComponent(name)
        try String(repeating: "x", count: bytes).write(to: url, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.modificationDate: Date().addingTimeInterval(-minutesAgo * 60)],
                                              ofItemAtPath: url.path)
        return url
    }

    func testNewestFirstWithoutTheCurrentLinkOrEmptyNotes() throws {
        let morning = try note("2026-01-15-1010-Chrome-通話-1010.md", minutesAgo: 200)
        let afternoon = try note("2026-01-15-1532-Chrome-通話-1531.md", minutesAgo: 90)
        _ = try note("2026-01-15-1940-會議-1940.md", minutesAgo: 30, bytes: 120)     // only the header
        _ = try note("notes.txt", minutesAgo: 10)
        try FileManager.default.createSymbolicLink(at: dir.appendingPathComponent("current.md"),
                                                   withDestinationURL: afternoon)
        let list = RecentNotes.list(in: dir)
        XCTAssertEqual(list.map(\.url.lastPathComponent), [afternoon.lastPathComponent, morning.lastPathComponent])
        XCTAssertEqual(list.first?.title, "1/15 15:32  Chrome 通話 1531")
    }

    func testLimitAndMissingFolder() throws {
        for i in 0..<15 { _ = try note("2026-01-1\(i % 10)-0\(i % 10)00-會議-\(i).md", minutesAgo: Double(i)) }
        XCTAssertEqual(RecentNotes.list(in: dir, limit: 10).count, 10)
        XCTAssertEqual(RecentNotes.list(in: dir.appendingPathComponent("missing")), [])
    }

    func testTitles() {
        func t(_ name: String) -> String { RecentNotes.title(for: URL(fileURLWithPath: "/x/\(name).md")) }
        XCTAssertEqual(t("2026-01-07-0740-Chrome-通話-0740"), "1/07 07:40  Chrome 通話 0740")
        XCTAssertEqual(t("2026-01-07-1915-會議"), "1/07 19:15  會議")
        XCTAssertEqual(t("2026-01-05-0930"), "1/05 09:30")
        XCTAssertEqual(t("my own notes"), "my own notes")                 // not ours: kept as it is
        XCTAssertEqual(t("2026-01-07-08a0-x"), "2026-01-07-08a0-x")
    }
}
