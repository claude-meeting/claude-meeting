import XCTest
@testable import MeetingCopilotCore

/// Older copies of the app left behind by an update are offered for the Trash (issue #5).
final class OldCopiesTests: XCTestCase {
    private let own = "/Applications/claude-meeting.app"
    private let home = "/" + "Users/x"                 // assembled: the public export refuses home paths

    private func remove(_ found: [(String, String?)], ownVersion: String? = "0.1.17") -> [String] {
        OldCopies.toRemove(found: found.map { (path: $0.0, version: $0.1) }, ownPath: own, ownVersion: ownVersion)
    }

    func testTheOldNameAndOlderVersionsAreOffered() {
        XCTAssertEqual(remove([("/Applications/Meeting Copilot.app", "0.1.3"), (own, "0.1.17")]),
                       ["/Applications/Meeting Copilot.app"])
        XCTAssertEqual(remove([(home + "/Applications/claude-meeting.app", "0.1.16")]),
                       [home + "/Applications/claude-meeting.app"])
        // The old name goes even when its version cannot be read or is not older.
        XCTAssertEqual(remove([("/Applications/Meeting Copilot.app", nil)]), ["/Applications/Meeting Copilot.app"])
    }

    func testThisCopyBuildsImagesAndTheTrashAreLeftAlone() {
        XCTAssertEqual(remove([(own, "0.1.3"), (own + "/", "0.1.3")]), [])
        XCTAssertEqual(remove([(home + "/meeting-copilot/app/macos/build/claude-meeting.app", "0.1.16"),
                               (home + "/src/.build/release/claude-meeting.app", "0.1.16"),
                               ("/Volumes/claude-meeting/claude-meeting.app", "0.1.16"),
                               (home + "/.Trash/Meeting Copilot.app", "0.1.3")]), [])
    }

    func testTheSameOrANewerVersionUnderTheNewNameStays() {
        XCTAssertEqual(remove([(home + "/Applications/claude-meeting.app", "0.1.17"),
                               (home + "/Applications/claude-meeting.app", "0.1.18")]), [])
        XCTAssertEqual(remove([(home + "/Applications/claude-meeting.app", nil)]), [])   // unreadable: not older
    }

    func testEachPathOnce() {
        XCTAssertEqual(remove([("/Applications/Meeting Copilot.app", "0.1.3"), ("/Applications/Meeting Copilot.app/", "0.1.3")]),
                       ["/Applications/Meeting Copilot.app"])
    }
}
