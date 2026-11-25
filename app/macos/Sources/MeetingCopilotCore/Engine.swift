import Foundation

/// What the background program (app/bin/autopilot.py) reports in state/status.json.
public struct EngineStatus: Decodable, Equatable {
    public var recording = false
    public var title = ""
    public var started_at: Double = 0
    public var auto = false
    public var manual = false
    public var paused = false
    public var processing = false
    public var notes = ""
    public var meetings_dir = ""
    public var language = ""
    public var open_viewer = true
    /// The speech model the current language needs but install.sh has not fetched (empty when all is there).
    public var asr_missing = ""
    public var problems: [String] = []
    /// "call" or "room" while recording (bin/autopilot.py RecordingMode), else empty.
    public var mode = ""
    public var updated: Double = 0

    public init() {}

    /// Field by field, each with its default: a status written by an older or newer background
    /// program (a field more or less) still decodes.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        func get<T: Decodable>(_ key: Key, _ fallback: T) -> T { (try? c.decodeIfPresent(T.self, forKey: key)) ?? nil ?? fallback }
        recording = get(.recording, false)
        title = get(.title, "")
        started_at = get(.started_at, 0)
        auto = get(.auto, false)
        manual = get(.manual, false)
        paused = get(.paused, false)
        processing = get(.processing, false)
        notes = get(.notes, "")
        meetings_dir = get(.meetings_dir, "")
        language = get(.language, "")
        open_viewer = get(.open_viewer, true)
        asr_missing = get(.asr_missing, "")
        problems = get(.problems, [])
        mode = get(.mode, "")
        updated = get(.updated, 0)
    }

    private enum Key: String, CodingKey {
        case recording, title, started_at, auto, manual, paused, processing, notes, meetings_dir, language,
             open_viewer, asr_missing, problems, updated, mode
    }
}

/// Values of ~/.config/meeting-copilot/config.toml the app needs before the engine answers
/// (paths); a plain line scan is enough for `key = "value"` entries.
public enum UserConfig {
    /// The user's config: MEETING_COPILOT_CONFIG when set (as the engine reads it), else the usual place.
    public static let file: URL = {
        if let path = ProcessInfo.processInfo.environment["MEETING_COPILOT_CONFIG"], !path.isEmpty {
            return URL(fileURLWithPath: path)
        }
        return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".config/meeting-copilot/config.toml")
    }()

    public static func string(_ section: String, _ key: String, file: URL = UserConfig.file) -> String? {
        guard let text = try? String(contentsOf: file, encoding: .utf8) else { return nil }
        var current = ""
        for raw in text.components(separatedBy: "\n") {
            let line = stripComment(raw).trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("[") {
                current = line.trimmingCharacters(in: CharacterSet(charactersIn: "[] "))
                    .split(separator: ".").map { $0.trimmingCharacters(in: .whitespaces) }.joined(separator: ".")
                continue
            }
            guard current == section, let eq = line.firstIndex(of: "=") else { continue }
            if line[..<eq].trimmingCharacters(in: .whitespaces) == key {
                var value = line[line.index(after: eq)...].trimmingCharacters(in: .whitespaces)
                if value.count >= 2, let q = value.first, q == "\"" || q == "'", value.last == q {
                    value = String(value.dropFirst().dropLast())
                    if q == "\"" {       // basic string: undo the two escapes a path or name can contain
                        value = value.replacingOccurrences(of: "\\\"", with: "\"").replacingOccurrences(of: "\\\\", with: "\\")
                    }
                }
                return value
            }
        }
        return nil
    }

    /// The line without a trailing comment; a `#` inside quotes is part of the value.
    private static func stripComment(_ line: String) -> String {
        var quote: Character? = nil
        var escaped = false
        for (i, c) in line.enumerated() {
            if let q = quote {
                if escaped { escaped = false } else if c == "\\" && q == "\"" { escaped = true } else if c == q { quote = nil }
            } else if c == "\"" || c == "'" {
                quote = c
            } else if c == "#" {
                return String(line.prefix(i))
            }
        }
        return line
    }

    public static func path(_ section: String, _ key: String, default fallback: String, file: URL = UserConfig.file) -> String {
        let v = string(section, key, file: file) ?? ""
        return NSString(string: v.isEmpty ? fallback : v).expandingTildeInPath
    }
}

/// What the live-notes window shows about the recording (2026-11-25: the mode was a small grey line in the menu,
/// and the user could not tell which mode it was in): its state, the mode, the switch, and start or stop.
public struct RecordingBar: Equatable {
    /// Strings keys: the state ("recording" or "not_recording"), the mode ("mode_short_call"/"mode_short_room", nil
    /// when not recording), the switch ("mode_switch_room"/"mode_switch_call", nil when not recording), and the
    /// action ("stop" or "start").
    public let state: String
    public let mode: String?
    public let switchTo: String?
    public let action: String
    /// The mode the switch sends ("room" or "call").
    public let switchMode: String?

    public init(state: String, mode: String?, switchTo: String?, action: String, switchMode: String?) {
        self.state = state; self.mode = mode; self.switchTo = switchTo; self.action = action
        self.switchMode = switchMode
    }

    public static func of(_ s: EngineStatus) -> RecordingBar {
        guard s.recording else {
            return RecordingBar(state: "not_recording", mode: nil, switchTo: nil, action: "start", switchMode: nil)
        }
        let room = s.mode == "room"
        return RecordingBar(state: "recording", mode: room ? "mode_short_room" : "mode_short_call",
                            switchTo: room ? "mode_switch_call" : "mode_switch_room", action: "stop",
                            switchMode: room ? "call" : "room")
    }

    /// "1:02:03" since the recording began (empty before it is known).
    public static func elapsed(since start: Double, now: Double) -> String {
        guard start > 0 else { return "" }
        let secs = max(0, Int(now - start))
        return String(format: "%d:%02d:%02d", secs / 3600, secs / 60 % 60, secs % 60)
    }
}
