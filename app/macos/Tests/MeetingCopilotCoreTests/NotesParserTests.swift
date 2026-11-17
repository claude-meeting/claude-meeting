import XCTest
@testable import MeetingCopilotCore

final class NotesParserTests: XCTestCase {

    // MARK: fixtures, block by block

    func testParsesChineseFixtureInOrder() throws {
        let entries = NotesParser.parse(try Fixtures.text("notes-zh.md"))
        let expected: [NoteBlock] = [
            .title("每週同步會議"),
            .started("開始：2026-01-15 10:00"),
            .time("10:00"),
            .transcript(time: "10:00:03", speaker: "其他人 A", text: "大家好"),
            .transcript(time: "10:00:07", speaker: "我", text: "好"),
            .transcript(time: "10:00:15", speaker: "其他人 A", text: "今天先過一下上週的進度"),
            .info("🔎 偵測到通話（會議軟體）"),
            .item("📌 決策：下週開始改用新的排程"),
            .item("💡 **問題**\n- 這是答案的第一點\n## 這行是答案裡的標題"),
            .transcript(time: "10:01:02", speaker: "其他人 B", text: "我們需要在月底前完成第一版"),
            .transcript(time: "10:01:02", speaker: "其他人 B", text: "我們需要在月底前完成第一版"),
            .answer(heading: "💡 什麼是第一版", body: "- 第一版指的是最小可用版本\n\n- 包含基本功能即可"),
            .time("10:05"),
            .info("👤 其他人 B 改名為 其他人 C"),
            .answer(heading: "❓ 月底前能完成嗎", body: "目前進度大約七成，應該可以。"),
            .info("👤 新講者加入：其他人 D"),
            .summary("討論上週進度，確認月底前完成第一版。"),
            .info("🎞️ 畫面：投影片第 3 頁"),
            .transcript(time: "10:05:40", speaker: "我", text: "那就這樣，下週見"),
        ]
        XCTAssertEqual(entries.map(\.block), expected)
    }

    func testParsesEnglishFixtureInOrder() throws {
        let entries = NotesParser.parse(try Fixtures.text("notes-en.md"))
        let expected: [NoteBlock] = [
            .title("Weekly sync"),
            .started("Started: 2026-01-15 10:00"),
            .time("10:00"),
            .transcript(time: "10:00:03", speaker: "Other A", text: "Hello everyone"),
            .transcript(time: "10:00:07", speaker: "Me", text: "Hi"),
            .transcript(time: "10:00:15", speaker: "Other A", text: "Let us go over last week first"),
            .info("🔎 Call detected (meeting app)"),
            .item("📌 Decision: switch to the new schedule next week"),
            .item("💡 **Question**\n- First point of the answer\n## A heading inside the answer"),
            .transcript(time: "10:01:02", speaker: "Other B", text: "We need the first version before the end of the month"),
            .transcript(time: "10:01:02", speaker: "Other B", text: "We need the first version before the end of the month"),
            .answer(heading: "💡 What is the first version",
                    body: "- The first version is the minimum usable build\n\n- Basic features are enough"),
            .time("10:05"),
            .info("👤 Other B renamed to Other C"),
            .answer(heading: "❓ Can it be done by month end", body: "About seventy percent is done, so it should be."),
            .info("👤 New speaker: Other D"),
            .summary("Reviewed last week's progress and confirmed the first version by month end."),
            .info("🎞️ Screen: slide 3"),
            .transcript(time: "10:05:40", speaker: "Me", text: "That is all, see you next week"),
        ]
        XCTAssertEqual(entries.map(\.block), expected)
    }

    // MARK: indented continuation

    func testIndentedLinesStayWithTheItemAbove() throws {
        for name in ["notes-zh.md", "notes-en.md"] {
            let blocks = NotesParser.parse(try Fixtures.text(name)).map(\.block)
            let items = blocks.compactMap { b -> String? in if case .item(let s) = b { return s } else { return nil } }
            XCTAssertEqual(items.count, 2, name)
            let question = items[1]
            XCTAssertEqual(question.components(separatedBy: "\n").count, 3, name)
            XCTAssertTrue(question.components(separatedBy: "\n")[1].hasPrefix("- "), name)
            XCTAssertTrue(question.components(separatedBy: "\n")[2].hasPrefix("## "), name)
            // Neither indented line became its own block.
            XCTAssertEqual(blocks.filter { if case .time = $0 { return true } else { return false } }.count, 2, name)
        }
    }

    func testIndentedLineWithoutItemAboveIsOrdinaryText() {
        let blocks = NotesParser.parse("# T\n  - not a continuation\n").map(\.block)
        XCTAssertEqual(blocks, [.title("T"), .item("not a continuation")])
    }

    // MARK: streamed answers

    func testAnswerKeepsBulletsHeadingsAndBlankLinesUntilTimeHeader() {
        let text = """
        ### 💡 q
        - one
        ## not a time header
        - two

        - three
        ## 11:00
        """
        let blocks = NotesParser.parse(text).map(\.block)
        XCTAssertEqual(blocks, [
            .answer(heading: "💡 q", body: "- one\n## not a time header\n- two\n\n- three"),
            .time("11:00"),
        ])
    }

    func testAnswerEndsAtSpeakerLine() {
        let blocks = NotesParser.parse("### ❓ q\nbody\n👤 someone\n- after").map(\.block)
        XCTAssertEqual(blocks, [.answer(heading: "❓ q", body: "body"), .info("👤 someone"), .item("after")])
    }

    func testAnswerEndsAtVideoLine() {
        let blocks = NotesParser.parse("### ❓ q\nbody\n🎞️ frame\n").map(\.block)
        XCTAssertEqual(blocks, [.answer(heading: "❓ q", body: "body"), .info("🎞️ frame")])
    }

    func testAnswerEndsAtTranscriptLine() {
        let blocks = NotesParser.parse("### 💡 q\nbody\n10:00:00 我｜hi\n").map(\.block)
        XCTAssertEqual(blocks, [.answer(heading: "💡 q", body: "body"), .transcript(time: "10:00:00", speaker: "我", text: "hi")])
    }

    func testAnswerDoesNotEndAtItemSummaryOrSearchLine() {
        let text = "### 💡 q\n- bullet\n**摘要**：s\n🔎 looked up\n# heading\nplain\n"
        let blocks = NotesParser.parse(text).map(\.block)
        XCTAssertEqual(blocks, [.answer(heading: "💡 q", body: "- bullet\n**摘要**：s\n🔎 looked up\n# heading\nplain")])
    }

    func testSecondAnswerHeadingClosesTheFirst() {
        let blocks = NotesParser.parse("### a\nx\n### b\ny\n").map(\.block)
        XCTAssertEqual(blocks, [.answer(heading: "a", body: "x"), .answer(heading: "b", body: "y")])
    }

    func testUnterminatedAnswerIsClosedAtEndOfFile() {
        let blocks = NotesParser.parse("### a\nline one\nline two").map(\.block)
        XCTAssertEqual(blocks, [.answer(heading: "a", body: "line one\nline two")])
    }

    // MARK: repeats and identity

    func testRepeatedSentencesAreAllKept() throws {
        for name in ["notes-zh.md", "notes-en.md"] {
            let entries = NotesParser.parse(try Fixtures.text(name))
            let repeated = entries.filter {
                if case .transcript(let t, _, _) = $0.block { return t == "10:01:02" } else { return false }
            }
            XCTAssertEqual(repeated.count, 2, name)
            XCTAssertEqual(repeated[0].block, repeated[1].block, name)
            XCTAssertNotEqual(repeated[0].id, repeated[1].id, name)
        }
        let twice = NotesParser.parse("- same\n- same\n- same\n")
        XCTAssertEqual(twice.map(\.id), [0, 1, 2])
        XCTAssertEqual(twice.map(\.block), [.item("same"), .item("same"), .item("same")])
    }

    func testIdsArePositionsAndStableWhenALineIsAppended() throws {
        for name in ["notes-zh.md", "notes-en.md"] {
            let text = try Fixtures.text(name)
            let before = NotesParser.parse(text)
            XCTAssertEqual(before.map(\.id), Array(0..<before.count), name)
            let after = NotesParser.parse(text + "10:06:00 Me｜one more line\n")
            XCTAssertEqual(after.count, before.count + 1, name)
            XCTAssertEqual(Array(after.prefix(before.count)), before, name)
            XCTAssertEqual(after.last?.id, before.count, name)
            XCTAssertEqual(after.last?.block, .transcript(time: "10:06:00", speaker: "Me", text: "one more line"), name)
        }
    }

    // MARK: small cases

    func testSummaryVariants() {
        XCTAssertEqual(NotesParser.parse("**摘要**：甲").map(\.block), [.summary("甲")])
        XCTAssertEqual(NotesParser.parse("摘要：乙").map(\.block), [.summary("乙")])
        XCTAssertEqual(NotesParser.parse("**Summary**: s").map(\.block), [.summary("s")])
    }

    func testBlankLinesAndWhitespaceAreIgnored() {
        XCTAssertEqual(NotesParser.parse("\n\n   \n").map(\.block), [])
        XCTAssertEqual(NotesParser.parse(" # padded \n").map(\.block), [.title("padded")])
    }

    func testTranscriptNeedsFullWidthBar() {
        XCTAssertEqual(NotesParser.parse("10:00:00 A|x").map(\.block), [.text("10:00:00 A|x")])
        XCTAssertEqual(NotesParser.parse("10:00:00 A｜").map(\.block), [.transcript(time: "10:00:00", speaker: "A", text: "")])
    }

    func testHighlightFlag() {
        XCTAssertFalse(NoteBlock.transcript(time: "", speaker: "", text: "").isHighlight)
        XCTAssertFalse(NoteBlock.text("").isHighlight)
        XCTAssertFalse(NoteBlock.started("").isHighlight)
        for b: NoteBlock in [.title(""), .time(""), .answer(heading: "", body: ""), .item(""), .summary(""), .info("")] {
            XCTAssertTrue(b.isHighlight, "\(b)")
        }
    }

    func testSimplifiedNotesAreReadToo() {
        // The engine's Simplified notes: their "started" line and the labels of what concerns the user.
        let blocks = NotesParser.parse("# Chrome 通话 1010（即时笔记）\n\n开始：10:10\n\n- ✅ 我的待办：寄规格\n").map(\.block)
        XCTAssertEqual(blocks.first { if case .started = $0 { return true } else { return false } }, .started("开始：10:10"))
        for item in ["✅ 我的待办：寄规格", "❓ 问我：进度呢", "🙋 点名：Alex", "✅ 我的待辦：寄規格", "❓ Asked me: status?"] {
            XCTAssertTrue(NotesParser.isMine(item), item)
        }
        XCTAssertFalse(NotesParser.isMine("📌 决策：下周上线"))
        XCTAssertFalse(NotesParser.isMine("✅ 待办：别人的事"))
    }
}
