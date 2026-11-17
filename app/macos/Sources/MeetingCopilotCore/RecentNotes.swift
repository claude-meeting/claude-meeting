import Foundation

/// A past meeting's live notes, for the "Recent meetings" menu.
public struct RecentNote: Equatable, Identifiable, Sendable {
    public let url: URL
    public let title: String
    public var id: String { url.path }

    public init(url: URL, title: String) {
        self.url = url
        self.title = title
    }
}

/// The live-notes files in `<meetings>/live`, newest first. The notes window shows only the meeting in
/// progress (it follows `current.md`), so without this list earlier notes, and the answers prepared in them,
/// could only be read as raw Markdown in Finder.
public enum RecentNotes {
    /// Notes this small hold only the header: a recording with nothing said (none worth listing).
    public static let minimumBytes = 200

    public static func list(in dir: URL, limit: Int = 10) -> [RecentNote] {
        let keys: [URLResourceKey] = [.contentModificationDateKey, .fileSizeKey, .isSymbolicLinkKey]
        guard let files = try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: keys,
                                                                       options: [.skipsHiddenFiles]) else { return [] }
        let notes: [(URL, Date)] = files.compactMap { url in
            guard url.pathExtension == "md", url.lastPathComponent != "current.md",
                  let v = try? url.resourceValues(forKeys: Set(keys)), v.isSymbolicLink != true,
                  (v.fileSize ?? 0) >= minimumBytes else { return nil }
            return (url, v.contentModificationDate ?? .distantPast)
        }
        return notes.sorted { $0.1 > $1.1 }.prefix(limit).map { RecentNote(url: $0.0, title: title(for: $0.0)) }
    }

    /// "2026-01-15-1010-Chrome-通話-1010.md" -> "1/15 10:10  Chrome 通話 1010"; other names are kept as they are.
    public static func title(for url: URL) -> String {
        let name = url.deletingPathExtension().lastPathComponent
        let parts = name.split(separator: "-", omittingEmptySubsequences: false).map(String.init)
        guard parts.count >= 4, parts[0].count == 4, parts[1].count == 2, parts[2].count == 2, parts[3].count == 4,
              ([parts[0], parts[1], parts[2], parts[3]].allSatisfy { $0.allSatisfy(\.isNumber) }) else { return name }
        let hhmm = parts[3]
        let rest = parts.dropFirst(4).joined(separator: " ")
        let when = "\(Int(parts[1]) ?? 0)/\(parts[2]) \(hhmm.prefix(2)):\(hhmm.suffix(2))"
        return rest.isEmpty ? when : "\(when)  \(rest)"
    }
}
