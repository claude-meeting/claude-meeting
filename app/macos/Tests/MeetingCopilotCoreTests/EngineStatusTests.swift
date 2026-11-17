import XCTest
@testable import MeetingCopilotCore

final class EngineStatusTests: XCTestCase {
    private func decode(_ json: String) throws -> EngineStatus {
        try JSONDecoder().decode(EngineStatus.self, from: Data(json.utf8))
    }

    func testEmptyObjectGivesDefaults() throws {
        let s = try decode("{}")
        XCTAssertEqual(s, EngineStatus())
        XCTAssertFalse(s.recording)
        XCTAssertEqual(s.title, "")
        XCTAssertEqual(s.started_at, 0)
        XCTAssertFalse(s.auto)
        XCTAssertFalse(s.manual)
        XCTAssertFalse(s.paused)
        XCTAssertFalse(s.processing)
        XCTAssertEqual(s.notes, "")
        XCTAssertEqual(s.meetings_dir, "")
        XCTAssertEqual(s.language, "")
        XCTAssertTrue(s.open_viewer, "open_viewer defaults to true")
        XCTAssertEqual(s.asr_missing, "")
        XCTAssertEqual(s.problems, [])
        XCTAssertEqual(s.updated, 0)
    }

    func testAllFieldsDecode() throws {
        let s = try decode("""
        {"recording": true, "title": "Sync", "started_at": 1700000000.25, "auto": true, "manual": true,
         "paused": true, "processing": true, "notes": "/tmp/n.md", "meetings_dir": "/tmp/m", "language": "zh",
         "open_viewer": false, "asr_missing": "org/model", "problems": ["a", "b"], "updated": 1700000001.5}
        """)
        XCTAssertEqual(s.asr_missing, "org/model")
        XCTAssertTrue(s.recording)
        XCTAssertEqual(s.title, "Sync")
        XCTAssertEqual(s.started_at, 1700000000.25)
        XCTAssertTrue(s.auto && s.manual && s.paused && s.processing)
        XCTAssertEqual(s.notes, "/tmp/n.md")
        XCTAssertEqual(s.meetings_dir, "/tmp/m")
        XCTAssertEqual(s.language, "zh")
        XCTAssertFalse(s.open_viewer)
        XCTAssertEqual(s.problems, ["a", "b"])
        XCTAssertEqual(s.updated, 1700000001.5)
    }

    func testMissingFieldsKeepDefaultsNextToPresentOnes() throws {
        let s = try decode(#"{"recording": true, "language": "en"}"#)
        XCTAssertTrue(s.recording)
        XCTAssertEqual(s.language, "en")
        XCTAssertTrue(s.open_viewer)
        XCTAssertEqual(s.updated, 0)
        XCTAssertEqual(s.problems, [])
    }

    func testWrongTypesFallBackToDefaults() throws {
        let s = try decode("""
        {"recording": "yes", "title": 5, "started_at": "soon", "open_viewer": "no", "problems": "none",
         "updated": "now", "notes": ["x"], "paused": 1}
        """)
        XCTAssertEqual(s, EngineStatus())
        XCTAssertTrue(s.open_viewer)
    }

    func testNullValuesFallBackToDefaults() throws {
        let s = try decode(#"{"recording": null, "open_viewer": null, "updated": null, "problems": null}"#)
        XCTAssertEqual(s, EngineStatus())
    }

    func testExtraFieldsAreIgnored() throws {
        let s = try decode(#"{"future_field": {"a": 1}, "another": [1, 2], "recording": true}"#)
        XCTAssertTrue(s.recording)
        XCTAssertEqual(s.title, "")
    }

    func testUpdatedIsDoubleAndAcceptsIntegerJSON() throws {
        let fractional = try decode(#"{"updated": 1700000000.75}"#)
        XCTAssertEqual(fractional.updated, 1700000000.75)
        let whole = try decode(#"{"updated": 1700000000}"#)
        XCTAssertEqual(whole.updated, 1700000000.0)
        XCTAssertTrue(type(of: whole.updated) == Double.self)
    }

    func testEqualityDistinguishesChangedFields() throws {
        let a = try decode(#"{"recording": true}"#)
        let b = try decode(#"{"recording": true}"#)
        let c = try decode(#"{"recording": false}"#)
        XCTAssertEqual(a, b)
        XCTAssertNotEqual(a, c)
    }

    func testNonObjectFails() {
        XCTAssertThrowsError(try decode("[]"))
        XCTAssertThrowsError(try decode("not json"))
    }
}
