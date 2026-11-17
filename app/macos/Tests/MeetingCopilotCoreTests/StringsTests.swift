import XCTest
@testable import MeetingCopilotCore

final class StringsTests: XCTestCase {
    private var saved = ""

    override func setUp() { saved = L.language }
    override func tearDown() { L.language = saved }

    func testTableIsNotEmptyAndKeysAreSorted() {
        XCTAssertGreaterThan(L.keys.count, 50)
        XCTAssertEqual(L.keys, L.keys.sorted())
        XCTAssertEqual(Set(L.keys).count, L.keys.count)
    }

    func testEveryKeyHasNonEmptyChineseAndEnglish() {
        for lang in ["zh", "zh-hans", "en"] {
            L.language = lang
            for key in L.keys {
                let value = L.t(key)
                XCTAssertFalse(value.isEmpty, "\(lang): \(key) is empty")
                XCTAssertNotEqual(value, key, "\(lang): \(key) falls back to its own key")
            }
        }
    }

    func testSimplifiedChineseHasEveryTextWrittenFromTheTraditionalOne() {
        XCTAssertEqual(Set(L.hans.keys), Set(L.keys), "run app/tools/hans_strings.py")
        L.language = "zh-hans"
        XCTAssertEqual(L.t("recording"), "录音中")
        XCTAssertEqual(L.t("s_web_verify"), "用网络搜索查证回答")          // mainland usage, not only the script
    }

    func testLanguageFamilies() {
        for (code, family) in [("zh-TW", "zh"), ("zh-Hant-TW", "zh"), ("zh-HK", "zh"), ("zh", "zh"), ("zh-CN", "zh-hans"),
                               ("zh-Hans-TW", "zh-hans"), ("zh_SG", "zh-hans"), ("zh-Hant-CN", "zh"), ("en-US", "en"),
                               ("", "en"), ("fr", "en")] {
            XCTAssertEqual(L.family(code), family, code)
        }
    }

    func testSwitchingLanguageChangesTheText() {
        L.language = "zh"
        XCTAssertEqual(L.t("recording"), "錄音中")
        XCTAssertEqual(L.t("summary"), "摘要")
        L.language = "en"
        XCTAssertEqual(L.t("recording"), "Recording")
        XCTAssertEqual(L.t("summary"), "Summary")
    }

    func testAnythingButZhReadsAsEnglish() {
        L.language = "fr"
        XCTAssertEqual(L.t("quit"), "Quit claude-meeting")
        L.language = "zh-Hant"           // the app normalises to "zh"; the raw tag is not Chinese here
        XCTAssertEqual(L.t("quit"), "Quit claude-meeting")
    }

    func testTheAppHasOneNameInEveryLanguage() {
        for lang in ["zh", "zh-hans", "en"] {
            L.language = lang
            for key in ["quit", "settings_title", "setup_title", "setup_mic_denied", "setup_login_detail"] {
                XCTAssertTrue(L.t(key).contains("claude-meeting"), "\(lang) \(key): \(L.t(key))")
            }
        }
        L.language = "zh-hans"
        XCTAssertTrue(L.t("quit").hasPrefix("退出"), L.t("quit"))     // macOS's own word in Simplified
        L.language = "en"
    }

    func testUnknownKeyReturnsTheKey() {
        L.language = "zh"
        XCTAssertEqual(L.t("no_such_key"), "no_such_key")
        L.language = "en"
        XCTAssertEqual(L.t("no_such_key"), "no_such_key")
    }

    func testFormatKeysKeepTheirPlaceholder() {
        for lang in ["zh", "zh-hans", "en"] {
            L.language = lang
            XCTAssertTrue(L.t("speakers_lines").contains("%d"), lang)
        }
    }
}
