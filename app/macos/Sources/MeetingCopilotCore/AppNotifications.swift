import Foundation

/// A notification the background program asks the app to show: one JSON file in `<state>/notifications`.
public struct AppNotification: Codable, Equatable, Sendable {
    public let title: String
    public let text: String
    public let sound: String
    public let open: String          // the notes a click opens; empty: the current meeting's

    public init(title: String, text: String, sound: String, open: String) {
        self.title = title
        self.text = text
        self.sound = sound
        self.open = open
    }
}

public enum AppNotifications {
    /// Older ones wrote to an app that had stopped: "someone called your name" is no use minutes later.
    public static let maxAge: TimeInterval = 60

    /// The notifications waiting in `dir`, oldest first. Each file is removed once read, unreadable or too old
    /// ones too. Dot files are still being written by the engine.
    public static func take(from dir: URL, now: Date = Date()) -> [AppNotification] {
        let fm = FileManager.default
        guard let files = try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.contentModificationDateKey],
                                                      options: [.skipsHiddenFiles]) else { return [] }
        var out: [AppNotification] = []
        for file in files.sorted(by: { $0.lastPathComponent < $1.lastPathComponent }) where file.pathExtension == "json" {
            let written = (try? file.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
            let data = try? Data(contentsOf: file)
            try? fm.removeItem(at: file)
            guard let written, now.timeIntervalSince(written) <= maxAge, let data,
                  let notification = try? JSONDecoder().decode(AppNotification.self, from: data) else { continue }
            out.append(notification)
        }
        return out
    }
}
