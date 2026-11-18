import AppKit
import MeetingCopilotCore
import UserNotifications

/// Shows the engine's notifications as this app's own, so that a click opens the notes (macOS credits a notification
/// sent with osascript to Script Editor, and a click on it opens Script Editor). The folder is watched rather than
/// only polled: a question to the user must not wait for the next poll.
@MainActor
final class Notifier: NSObject, UNUserNotificationCenterDelegate {
    var onOpen: ((URL?) -> Void)?
    private var center: UNUserNotificationCenter?
    private var watched: URL?
    private var source: DispatchSourceFileSystemObject?

    func start() {
        guard Bundle.main.bundleIdentifier != nil else { return }    // the bare test binary has no notification centre
        let c = UNUserNotificationCenter.current()
        c.delegate = self
        c.requestAuthorization(options: [.alert, .sound]) { _, _ in }
        center = c
    }

    /// Every engine poll: shows what is waiting, and catches anything the folder watch missed.
    func check(_ dir: URL) {
        if dir != watched { watch(dir) }
        let waiting = AppNotifications.take(from: dir)
        guard !waiting.isEmpty else { return }
        guard let center else {
            waiting.forEach(viaScript)
            return
        }
        center.getNotificationSettings { settings in
            let allowed = settings.authorizationStatus == .authorized
            Task { @MainActor [weak self] in
                waiting.forEach { allowed ? self?.show($0) : self?.viaScript($0) }
            }
        }
    }

    private func watch(_ dir: URL) {
        source?.cancel()
        source = nil
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let fd = open(dir.path, O_EVTONLY)
        guard fd >= 0 else { return }
        watched = dir
        let s = DispatchSource.makeFileSystemObjectSource(fileDescriptor: fd, eventMask: .write, queue: .main)
        s.setEventHandler { [weak self] in
            MainActor.assumeIsolated { self?.check(dir) }
        }
        s.setCancelHandler { close(fd) }
        s.resume()
        source = s
    }

    func show(_ n: AppNotification) {
        let content = UNMutableNotificationContent()
        content.title = n.title
        content.body = n.text
        content.userInfo = ["open": n.open]
        if !n.sound.isEmpty { content.sound = UNNotificationSound(named: UNNotificationSoundName("\(n.sound).aiff")) }
        center?.add(UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil)) { error in
            guard error != nil else { return }
            Task { @MainActor [weak self] in self?.viaScript(n) }
        }
    }

    /// Notifications are not allowed for the app (or not decided yet): better one that opens Script Editor than none.
    private func viaScript(_ n: AppNotification) {
        let script = "on run argv\n display notification (item 2 of argv) with title (item 1 of argv)"
            + " sound name (item 3 of argv)\nend run"
        Task { _ = await Shell.run("/usr/bin/osascript", ["-e", script, n.title, n.text, n.sound]) }
    }

    /// Shown even while this app is in front, e.g. with the notes window focused.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                            withCompletionHandler done: @escaping (UNNotificationPresentationOptions) -> Void) {
        done([.banner, .sound, .list])
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                            withCompletionHandler done: @escaping () -> Void) {
        let clicked = response.actionIdentifier == UNNotificationDefaultActionIdentifier
        let path = response.notification.request.content.userInfo["open"] as? String ?? ""
        Task { @MainActor [weak self] in
            if clicked {
                let exists = !path.isEmpty && FileManager.default.fileExists(atPath: path)
                self?.onOpen?(exists ? URL(fileURLWithPath: path) : nil)
            }
            done()
        }
    }
}
