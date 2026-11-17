import XCTest
@testable import MeetingCopilotCore

final class UserConfigTests: XCTestCase {
    private var dir: URL!
    private var toml: URL!

    override func setUpWithError() throws {
        dir = try Fixtures.tempDir()
        toml = dir.appendingPathComponent("config.toml")
        try """
        # a comment line at the top
        title = "top level, no section"

        [user]
        name = "Test User # not a comment"   # a trailing comment
        nick = 'single # quoted'
        bare = plain value # comment
        spaced   =    "around the equals"
        language = "zh"

        [ paths . sub ]   # dotted header with spaces and a trailing comment
        dir = "~/some/dir"
        quoted = "say \\"hi\\" and a \\\\ backslash"
        raw = 'kept \\"as is\\" \\\\'
        empty = ""

        [voice]
        voiceprint_file = "~/voice.json"
        """.write(to: toml, atomically: true, encoding: .utf8)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: dir)
    }

    func testHashInsideQuotesIsPartOfTheValue() {
        XCTAssertEqual(UserConfig.string("user", "name", file: toml), "Test User # not a comment")
    }

    func testSingleQuotedValue() {
        XCTAssertEqual(UserConfig.string("user", "nick", file: toml), "single # quoted")
    }

    func testBareValueLosesTrailingComment() {
        XCTAssertEqual(UserConfig.string("user", "bare", file: toml), "plain value")
        XCTAssertEqual(UserConfig.string("user", "spaced", file: toml), "around the equals")
    }

    func testDottedHeaderWithSpacesAndComment() {
        XCTAssertEqual(UserConfig.string("paths.sub", "dir", file: toml), "~/some/dir")
        XCTAssertNil(UserConfig.string("paths", "dir", file: toml))
        XCTAssertNil(UserConfig.string(" paths . sub ", "dir", file: toml))
    }

    func testBasicStringEscapes() {
        XCTAssertEqual(UserConfig.string("paths.sub", "quoted", file: toml), "say \"hi\" and a \\ backslash")
    }

    func testLiteralStringKeepsBackslashes() {
        XCTAssertEqual(UserConfig.string("paths.sub", "raw", file: toml), #"kept \"as is\" \\"#)
    }

    func testMissingKeySectionOrFileIsNil() {
        XCTAssertNil(UserConfig.string("user", "missing", file: toml))
        XCTAssertNil(UserConfig.string("nope", "name", file: toml))
        // Keys before any header live in section "".
        XCTAssertEqual(UserConfig.string("", "title", file: toml), "top level, no section")
        XCTAssertNil(UserConfig.string("user", "name", file: dir.appendingPathComponent("absent.toml")))
    }

    func testKeyInAnotherSectionIsNotFound() {
        XCTAssertNil(UserConfig.string("voice", "name", file: toml))
        XCTAssertEqual(UserConfig.string("voice", "voiceprint_file", file: toml), "~/voice.json")
    }

    func testPathExpandsTilde() {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        XCTAssertEqual(UserConfig.path("paths.sub", "dir", default: "/x", file: toml), home + "/some/dir")
        XCTAssertEqual(UserConfig.path("voice", "voiceprint_file", default: "/x", file: toml), home + "/voice.json")
    }

    func testPathFallsBackWhenMissingOrEmptyAndExpandsTheDefault() {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        XCTAssertEqual(UserConfig.path("paths.sub", "empty", default: "~/fallback", file: toml), home + "/fallback")
        XCTAssertEqual(UserConfig.path("paths.sub", "missing", default: "/Applications/X.app", file: toml), "/Applications/X.app")
        XCTAssertEqual(UserConfig.path("a", "b", default: "~/d", file: dir.appendingPathComponent("absent.toml")), home + "/d")
    }

    func testDefaultFileIsTheUserConfig() {
        XCTAssertEqual(UserConfig.file.path,
                       FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".config/meeting-copilot/config.toml").path)
    }
}
