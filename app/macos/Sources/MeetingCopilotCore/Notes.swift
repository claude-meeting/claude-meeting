import Foundation

/// One piece of the live notes file, as the background program writes it.
public enum NoteBlock: Identifiable, Equatable {
    case title(String)
    case started(String)
    case time(String)                                  // ## 19:42
    case transcript(time: String, speaker: String, text: String)
    case answer(heading: String, body: String)        // ### 💡 / ❓ question, then the streamed answer
    case item(String)                                  // - 📌 決策：…
    case summary(String)
    case info(String)                                  // 👤 / 🎞️ / 🔎 lines
    case text(String)

    public var id: String {
        switch self {
        case .title(let s), .started(let s), .time(let s), .item(let s), .summary(let s), .info(let s), .text(let s):
            return s
        case .transcript(let t, let sp, let x): return t + sp + x
        case .answer(let h, _): return "answer:" + h
        }
    }

    public var isHighlight: Bool {
        switch self {
        case .transcript, .text, .started: return false
        default: return true
        }
    }
}

public enum NotesParser {
    /// An item that concerns the user (mentioned, my to-do, asked me), in any language the engine writes.
    public static func isMine(_ item: String) -> Bool {
        item.contains("★") || NotesMarks.mine.contains { item.contains($0) }
    }

    static let transcriptLine = #/^(\d{2}:\d{2}:\d{2}) (.+?)｜(.*)$/#

    static let timeHeader = #/^## \d{2}:\d{2}$/#

    /// Blocks in file order. Position is the identity: identical lines (a question answered twice,
    /// the same transcript line in one second) are all shown.
    public static func parse(_ text: String) -> [NoteEntry] {
        var blocks: [NoteBlock] = []
        var answerHeading: String?
        var answerBody: [String] = []

        func closeAnswer() {
            if let h = answerHeading {
                blocks.append(.answer(heading: h, body: answerBody.joined(separator: "\n")
                    .trimmingCharacters(in: .whitespacesAndNewlines)))
            }
            answerHeading = nil
            answerBody = []
        }

        func continueItem(_ s: String) -> Bool {
            if case .item(let prev)? = blocks.last {         // an indented line belongs to the item above
                blocks[blocks.count - 1] = .item(prev + "\n" + s)
                return true
            }
            return false
        }

        for raw in text.components(separatedBy: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            // An indented line continues the item above it (tick answers are written that way), whatever
            // it starts with: "- x" or "## x" inside an answer must not become its own block.
            if answerHeading == nil, raw.hasPrefix("  "), continueItem(line) { continue }
            if line.hasPrefix("### ") {
                closeAnswer()
                answerHeading = String(line.dropFirst(4))
                continue
            }
            if let m = line.wholeMatch(of: transcriptLine) {
                closeAnswer()
                blocks.append(.transcript(time: String(m.1), speaker: String(m.2), text: String(m.3)))
                continue
            }
            if answerHeading != nil {
                // Inside a streamed answer only a time header or a speaker/video note ends it: the
                // answer's own bullets and headings stay with it.
                if line.wholeMatch(of: timeHeader) != nil || line.hasPrefix("👤") || line.hasPrefix("🎞️") {
                    closeAnswer()
                } else {
                    answerBody.append(raw)
                    continue
                }
            }
            if line.hasPrefix("## ") {
                blocks.append(.time(String(line.dropFirst(3))))
                continue
            }
            if line.hasPrefix("# ") {
                blocks.append(.title(String(line.dropFirst(2))))
                continue
            }
            if line.hasPrefix("- ") {
                blocks.append(.item(String(line.dropFirst(2))))
                continue
            }
            if line.hasPrefix("**摘要**：") || line.hasPrefix("摘要：") || line.hasPrefix("**Summary**:") {
                let body = line.replacingOccurrences(of: "**摘要**：", with: "").replacingOccurrences(of: "摘要：", with: "")
                    .replacingOccurrences(of: "**Summary**: ", with: "")
                blocks.append(.summary(body))
                continue
            }
            if line.hasPrefix("🔎") || line.hasPrefix("👤") || line.hasPrefix("🎞️") {
                blocks.append(.info(line))
                continue
            }
            if NotesMarks.started.contains(where: line.hasPrefix) {
                blocks.append(.started(line))
                continue
            }
            if line.isEmpty { continue }
            blocks.append(.text(line))
        }
        closeAnswer()
        return blocks.enumerated().map { NoteEntry(id: $0.offset, block: $0.element) }
    }
}

public struct NoteEntry: Identifiable, Equatable {
    public let id: Int
    public let block: NoteBlock

    public init(id: Int, block: NoteBlock) {
        self.id = id
        self.block = block
    }
}
