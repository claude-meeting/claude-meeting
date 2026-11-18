import Foundation

/// Older copies of the app left on disk by an update. They share this app's bundle id, so macOS can show
/// notifications with the old copy's name and icon, and an old copy may be opened instead of this one: before
/// 0.1.17 the app was called "Meeting Copilot.app", so dragging the new one to Applications left the old one there.
public enum OldCopies {
    public static let oldName = "Meeting Copilot.app"

    /// Of the app bundles found with this app's bundle id (path and CFBundleShortVersionString; the caller checks
    /// the id), the ones to offer to move to the Trash: not this copy, not a build folder, a mounted disk image or the
    /// Trash, and either under the old name or of a readable, older version.
    public static func toRemove(found: [(path: String, version: String?)], ownPath: String,
                                ownVersion: String?) -> [String] {
        let own = (ownPath as NSString).standardizingPath
        var seen = Set<String>()
        return found.compactMap { copy in
            let path = (copy.path as NSString).standardizingPath
            guard path != own, seen.insert(path).inserted else { return nil }
            for skip in ["/build/", "/.build/", "/DerivedData/", "/.Trash/"] where path.contains(skip) { return nil }
            if path.hasPrefix("/Volumes/") { return nil }
            // A version that cannot be read is not taken for older: only the old name says so then.
            let old = (path as NSString).lastPathComponent == oldName
                || (copy.version != nil && SingleInstance.compare(copy.version, ownVersion) < 0)
            return old ? path : nil
        }
    }
}
