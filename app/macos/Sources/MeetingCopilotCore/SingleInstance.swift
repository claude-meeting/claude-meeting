import Foundation

/// Which copy of the app keeps running when another is already open. Two copies showed two menu bar icons and could
/// post each notification twice (a click on a notification of the installed copy can make macOS start a newer copy
/// in the build folder as a second app).
public enum SingleInstance {
    /// Another running copy: its CFBundleShortVersionString (nil when it cannot be read, or when its file was
    /// replaced since it started: what runs is then older than the file says) and whether it started before this one.
    public struct Copy: Equatable {
        public let version: String?
        public let startedFirst: Bool
        public init(version: String?, startedFirst: Bool = true) {
            self.version = version
            self.startedFirst = startedFirst
        }
    }

    public enum Decision: Equatable {
        /// No other copy to mind: start as usual.
        case run
        /// Another copy is newer, or as new and started first: bring it to the front and quit (its index in `others`).
        case handOver(to: Int)
        /// These older copies quit, this one runs (an update opened from a notification or Finder).
        case replace([Int])
    }

    /// `own` is this copy's version; a version that cannot be read counts as oldest. Of two copies as new as each
    /// other the one that started first stays, so two started together never both hand over and leave none.
    public static func decide(own: String?, others: [Copy]) -> Decision {
        guard !others.isEmpty else { return .run }
        let newest = others.indices.max {
            let order = compare(others[$0].version, others[$1].version)
            return order != 0 ? order < 0 : !others[$0].startedFirst && others[$1].startedFirst
        }!
        let order = compare(others[newest].version, own)
        if order > 0 || (order == 0 && others[newest].startedFirst) { return .handOver(to: newest) }
        // A copy as new as this one that started after it hands over by itself; the older ones are told to quit.
        let older = others.indices.filter { compare(others[$0].version, own) < 0 }
        return older.isEmpty ? .run : .replace(older)
    }

    /// What a copy that hands over asks the running copy to show: the notes (of a file, or the current ones when
    /// the path is empty), the settings or the setup guide.
    public enum Request: Equatable {
        case notes(String), settings, setup

        public var text: String {
            switch self {
            case .notes(let path): return "notes:" + path
            case .settings: return "settings"
            case .setup: return "setup"
            }
        }

        public init?(_ text: String) {
            switch text {
            case "settings": self = .settings
            case "setup": self = .setup
            case _ where text.hasPrefix("notes:"): self = .notes(String(text.dropFirst(6)))
            default: return nil
            }
        }
    }

    /// <0, 0 or >0 as version `a` is older than, the same as or newer than `b` ("0.1.9" < "0.1.15"; nil is oldest).
    public static func compare(_ a: String?, _ b: String?) -> Int {
        switch (parts(a), parts(b)) {
        case (nil, nil): return 0
        case (nil, _): return -1
        case (_, nil): return 1
        case let (x?, y?):
            for i in 0..<max(x.count, y.count) {
                let l = i < x.count ? x[i] : 0, r = i < y.count ? y[i] : 0
                if l != r { return l < r ? -1 : 1 }
            }
            return 0
        }
    }

    private static func parts(_ version: String?) -> [Int]? {
        guard let version, !version.isEmpty else { return nil }
        let numbers = version.split(separator: ".").map { Int($0.prefix { $0.isNumber }) }
        return numbers.contains(nil) ? nil : numbers.compactMap { $0 }
    }

    /// Posted (distributed) by a copy that hands over; its object is a `Request`'s text.
    public static let open = Notification.Name("com.meeting-copilot.app.open")
}
