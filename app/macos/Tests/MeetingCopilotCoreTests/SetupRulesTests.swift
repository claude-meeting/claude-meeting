import XCTest
@testable import MeetingCopilotCore

/// The setup guide's step order, as a fresh Mac meets it.
final class SetupRulesTests: XCTestCase {
    func testInstallWaitsForMinutes() {
        // On a fresh Mac, Install could be pressed before Minutes was installed, and install.sh then stopped.
        XCTAssertEqual(SetupRules.installBlocked(name: "Alex", minutesReady: false, engineBusy: false),
                       "setup_needs_minutes")
        XCTAssertNil(SetupRules.installBlocked(name: "Alex", minutesReady: true, engineBusy: false))
    }

    func testMeetingInProgressBlocksFirst() {
        XCTAssertEqual(SetupRules.installBlocked(name: "Alex", minutesReady: false, engineBusy: true), "setup_busy")
        XCTAssertEqual(SetupRules.installBlocked(name: "Alex", minutesReady: true, engineBusy: true), "setup_busy")
    }

    func testNameIsRequiredWithoutATooltip() {
        XCTAssertEqual(SetupRules.installBlocked(name: "", minutesReady: true, engineBusy: false), "")
        XCTAssertEqual(SetupRules.installBlocked(name: "   ", minutesReady: true, engineBusy: false), "")
    }

    func testTooltipKeysExistInBothLanguages() {
        for key in ["setup_busy", "setup_needs_minutes"] {
            XCTAssertTrue(L.keys.contains(key), key)
        }
    }

    func testEngineDoneNeedsTheLanguagesModel() {
        XCTAssertTrue(SetupRules.engineDone(installed: true, outdated: false, asrMissing: ""))
        XCTAssertFalse(SetupRules.engineDone(installed: false, outdated: false, asrMissing: ""))
        XCTAssertFalse(SetupRules.engineDone(installed: true, outdated: true, asrMissing: ""))
        // The language was changed after install; the new language's model is not there yet.
        XCTAssertFalse(SetupRules.engineDone(installed: true, outdated: false, asrMissing: "JacobLinCool/TEA-ASR-1.1"))
    }

    func testVoiceprintWaitsForSpeechRecognition() {
        XCTAssertFalse(SetupRules.voiceEnabled(engineDone: false, recording: false))
        XCTAssertFalse(SetupRules.voiceEnabled(engineDone: true, recording: true))
        XCTAssertTrue(SetupRules.voiceEnabled(engineDone: true, recording: false))
    }

    func testFreshMacWalkthrough() {
        // The order a new user goes through: nothing, then Minutes, then speech recognition, then the voiceprint.
        var minutes = false, engine = false
        XCTAssertNotNil(SetupRules.installBlocked(name: "Alex", minutesReady: minutes, engineBusy: false))
        XCTAssertFalse(SetupRules.voiceEnabled(engineDone: engine, recording: false))
        minutes = true
        XCTAssertNil(SetupRules.installBlocked(name: "Alex", minutesReady: minutes, engineBusy: false))
        engine = SetupRules.engineDone(installed: true, outdated: false, asrMissing: "")
        XCTAssertTrue(SetupRules.voiceEnabled(engineDone: engine, recording: false))
        XCTAssertFalse(SetupRules.allDone(minutes: minutes, model: false, engine: engine))
        XCTAssertTrue(SetupRules.allDone(minutes: minutes, model: true, engine: engine))
    }

    func testTheModelStepIsDoneBySignInOrByAnotherServicesAddressAndModel() {
        XCTAssertTrue(SetupRules.modelReady(provider: nil, claudeSignedIn: true, baseURL: nil, model: nil))
        XCTAssertFalse(SetupRules.modelReady(provider: "claude", claudeSignedIn: false, baseURL: "http://x/v1", model: "m"))
        // Another service: Claude's sign-in no longer matters, the address and the model's name do.
        XCTAssertTrue(SetupRules.modelReady(provider: "openai", claudeSignedIn: false, baseURL: "http://localhost:11434/v1", model: "m"))
        XCTAssertFalse(SetupRules.modelReady(provider: "openai", claudeSignedIn: true, baseURL: "", model: "m"))
        XCTAssertFalse(SetupRules.modelReady(provider: "openai", claudeSignedIn: true, baseURL: "http://x/v1", model: "  "))
        XCTAssertFalse(SetupRules.modelReady(provider: "openai", claudeSignedIn: true, baseURL: nil, model: nil))
    }
}
