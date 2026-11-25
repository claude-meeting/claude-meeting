import XCTest
@testable import MeetingCopilotCore

/// One copy of the app at a time, the newest one.
final class SingleInstanceTests: XCTestCase {
    func testAnAppReplacedOnDiskSinceItStartedIsOld() {
        let start = Date(timeIntervalSince1970: 1_000)
        XCTAssertTrue(SingleInstance.replaced(executableChanged: start.addingTimeInterval(60), launched: start))
        XCTAssertFalse(SingleInstance.replaced(executableChanged: start.addingTimeInterval(-60), launched: start))
        XCTAssertFalse(SingleInstance.replaced(executableChanged: nil, launched: start))
        // Copied from a disk image: the file keeps the build's date, the installed version is not the one running.
        XCTAssertTrue(SingleInstance.replaced(executableChanged: start.addingTimeInterval(-86_400), launched: start,
                                              running: "0.1.20 (20)", installed: "0.1.21 (21)"))
        XCTAssertFalse(SingleInstance.replaced(executableChanged: start.addingTimeInterval(-60), launched: start,
                                               running: "0.1.21 (21)", installed: "0.1.21 (21)"))
        XCTAssertEqual(SingleInstance.version(["CFBundleShortVersionString": "0.1.21", "CFBundleVersion": "21"]),
                       "0.1.21 (21)")
        XCTAssertNil(SingleInstance.version(nil))
    }

    private func copies(_ versions: String?...) -> [SingleInstance.Copy] { versions.map { .init(version: $0) } }

    func testAloneRuns() {
        XCTAssertEqual(SingleInstance.decide(own: "0.1.15", others: []), .run)
    }

    func testANewerCopyReplacesAnOlderOne() {
        // An older copy was running when a notification click started a newer one: the newer one stays.
        XCTAssertEqual(SingleInstance.decide(own: "0.1.15", others: copies("0.1.13")), .replace([0]))
        XCTAssertEqual(SingleInstance.decide(own: "0.1.15", others: copies("0.1.13", nil)), .replace([0, 1]))
    }

    func testTheSameOrAnOlderCopyHandsOver() {
        XCTAssertEqual(SingleInstance.decide(own: "0.1.15", others: copies("0.1.15")), .handOver(to: 0))
        XCTAssertEqual(SingleInstance.decide(own: "0.1.13", others: copies("0.1.15")), .handOver(to: 0))
        XCTAssertEqual(SingleInstance.decide(own: "0.1.13", others: copies("0.1.9", "0.1.16", "0.1.14")), .handOver(to: 1))
        XCTAssertEqual(SingleInstance.decide(own: nil, others: copies(nil)), .handOver(to: 0))
    }

    func testOfTwoStartedTogetherTheFirstStays() {
        // Each sees the other: if both handed over, none would be left.
        let later = SingleInstance.Copy(version: "0.1.15", startedFirst: false)
        XCTAssertEqual(SingleInstance.decide(own: "0.1.15", others: [later]), .run)
        XCTAssertEqual(SingleInstance.decide(own: "0.1.15", others: copies("0.1.15")), .handOver(to: 0))
        // The one that started first is the one handed over to; an older copy still quits.
        XCTAssertEqual(SingleInstance.decide(own: "0.1.15", others: [later, .init(version: "0.1.15")]), .handOver(to: 1))
        XCTAssertEqual(SingleInstance.decide(own: "0.1.15", others: [later, .init(version: "0.1.13")]), .replace([1]))
        // A newer copy stays whichever started first.
        XCTAssertEqual(SingleInstance.decide(own: "0.1.15", others: [.init(version: "0.1.16", startedFirst: false)]),
                       .handOver(to: 0))
    }

    func testACopyReplacedOnDiskSinceItStartedCountsAsOlder() {
        // An update copied over the running app: the file says 0.1.16, what runs is older (its version is given as nil).
        XCTAssertEqual(SingleInstance.decide(own: "0.1.16", others: copies(nil)), .replace([0]))
    }

    func testRequestsSurviveTheTrip() {
        for request in [SingleInstance.Request.notes(""), .notes("/tmp/a b/notes:1.md"), .settings, .setup] {
            XCTAssertEqual(SingleInstance.Request(request.text), request)
        }
        XCTAssertNil(SingleInstance.Request("something else"))
    }

    func testVersionsCompareAsNumbers() {
        XCTAssertLessThan(SingleInstance.compare("0.1.9", "0.1.15"), 0)
        XCTAssertEqual(SingleInstance.compare("0.1", "0.1.0"), 0)
        XCTAssertGreaterThan(SingleInstance.compare("1.0", "0.9.9"), 0)
        XCTAssertGreaterThan(SingleInstance.compare("0.1.2b1", "0.1.1"), 0)     // a suffix is ignored
        XCTAssertLessThan(SingleInstance.compare("dev", "0.1.0"), 0)             // unreadable: oldest
        XCTAssertLessThan(SingleInstance.compare(nil, "0.1.0"), 0)
        XCTAssertGreaterThan(SingleInstance.compare("0.1.0", ""), 0)
    }
}
