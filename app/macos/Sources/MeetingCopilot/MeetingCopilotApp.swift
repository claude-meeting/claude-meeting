import AppKit
import SwiftUI
import MeetingCopilotCore

@main
struct MeetingCopilotApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        MenuBarExtra {
            MenuContent(engine: delegate.engine, openNotes: delegate.showNotes,
                        openNotesFile: { delegate.showNotes($0) }, openSettings: delegate.showSettings,
                        openSetup: delegate.showSetup, openPeople: delegate.showPeople)
        } label: {
            MenuIcon(engine: delegate.engine)
        }
        .menuBarExtraStyle(.window)
    }
}

/// Owns the engine bridge and the notes window (an AppKit window, so it can open by itself when a
/// meeting starts, without the menu being open).
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    let engine = Engine()
    let notes = NotesModel()
    lazy var settings = SettingsModel(engine: engine)
    lazy var setup = SetupModel(engine: engine)
    lazy var speakers = SpeakersModel(engine: engine)
    let people = PeopleModel()
    let notifier = Notifier()
    private var handingOver: NSRunningApplication?     // the copy of the app that runs on: this one quits (oneCopy())
    private var setupWindow: NSWindow?
    private var extraWindows: [String: NSWindow] = [:]

    /// A simple window per key, created once and brought to the front afterwards.
    private func showWindow<V: View>(_ key: String, _ title: String, size: NSSize, _ view: () -> V) {
        if extraWindows[key] == nil {
            let w = NSWindow(contentRect: NSRect(origin: .zero, size: size),
                             styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
            w.title = title
            w.isReleasedWhenClosed = false
            w.contentView = NSHostingView(rootView: view())
            w.center()
            extraWindows[key] = w
        }
        NSApp.activate(ignoringOtherApps: true)
        extraWindows[key]?.makeKeyAndOrderFront(nil)
    }

    func showSpeakers() {
        speakers.load()
        showWindow("speakers", L.t("speakers_title"), size: NSSize(width: 460, height: 320)) { SpeakersView(model: speakers) }
    }

    func showPeople() {
        people.load()
        showWindow("people", L.t("people_title"), size: NSSize(width: 480, height: 420)) { PeopleView(model: people) }
    }

    /// Window titles follow the engine's language once it is known.
    func retitleWindows() {
        window?.title = L.t("notes_title")
        settingsWindow?.title = L.t("settings_title")
        setupWindow?.title = L.t("setup_title")
        extraWindows["speakers"]?.title = L.t("speakers_title")
        extraWindows["people"]?.title = L.t("people_title")
    }

    func windowWillClose(_ notification: Notification) {
        guard let w = notification.object as? NSWindow else { return }
        if w === setupWindow {
            setup.stopChecking()
            if engine.installed { UserDefaults.standard.set(true, forKey: "setupShown") }
        } else if w === window {
            notes.stop()
        }
    }

    func showSetup() {
        setup.startChecking()
        if setupWindow == nil {
            let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 600, height: 680),
                             styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
            w.title = L.t("setup_title")
            w.isReleasedWhenClosed = false
            w.delegate = self
            w.contentView = NSHostingView(rootView: SetupView(model: setup, openSettings: { [weak self] in self?.showSettings() }) { [weak w] in
                UserDefaults.standard.set(true, forKey: "setupShown")
                w?.close()
            })
            w.center()
            setupWindow = w
        }
        NSApp.activate(ignoringOtherApps: true)
        setupWindow?.makeKeyAndOrderFront(nil)
    }
    private var window: NSWindow?
    private var settingsWindow: NSWindow?

    func showSettings() {
        if settingsWindow == nil {
            let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 520, height: 680),
                             styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
            w.title = L.t("settings_title")
            w.isReleasedWhenClosed = false
            w.contentView = NSHostingView(rootView: SettingsView(model: settings))
            w.center()
            settingsWindow = w
        } else {
            settings.load()
        }
        NSApp.activate(ignoringOtherApps: true)
        settingsWindow?.makeKeyAndOrderFront(nil)
    }

    /// Before launch ends: a click on a notification can be what starts the app.
    func applicationWillFinishLaunching(_ notification: Notification) {
        let args = CommandLine.arguments
        if args.contains("--snapshot") || args.contains("--settings-dump") {
            NSApp.setActivationPolicy(.accessory)                   // a tool run: no Dock icon
        }
        if oneCopy() {
            NSApp.setActivationPolicy(.accessory)                   // about to quit: the copy that runs on has the icon
            return
        }
        takeRequests()
    }

    /// A click on the Dock icon (or opening the app again) while no window is open shows the notes.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag && handingOver == nil { showNotes() }
        return true
    }

    /// Clicks on this copy's notifications, and what a copy started later hands over, open here.
    private func takeRequests() {
        notifier.onOpen = { [weak self] url in self?.showNotes(url ?? self?.engine.currentNotes) }
        notifier.start()
        DistributedNotificationCenter.default().addObserver(forName: SingleInstance.open, object: nil,
                                                            queue: .main) { [weak self] note in
            guard let request = SingleInstance.Request(note.object as? String ?? "") else { return }
            Task { @MainActor in
                guard let self else { return }
                switch request {
                case .notes(let path): self.showNotes(path.isEmpty ? self.engine.currentNotes : URL(fileURLWithPath: path))
                case .settings: self.showSettings()
                case .setup: self.showSetup()
                }
            }
        }
    }

    /// One copy of the app at a time, the newest (SingleInstance). True when this copy hands over to another and
    /// quits; what it was started for (a notification click, --show-notes, --show-settings, --show-setup) opens in
    /// the other copy.
    private func oneCopy() -> Bool {
        let args = CommandLine.arguments
        guard let id = Bundle.main.bundleIdentifier, !args.contains("--snapshot"), !args.contains("--settings-dump")
        else { return false }                                       // tools and tests, not a second app
        let me = NSRunningApplication.current
        let others = NSRunningApplication.runningApplications(withBundleIdentifier: id)
            .filter { $0.processIdentifier != me.processIdentifier && !$0.isTerminated }
        let version = { (url: URL?) in url.flatMap { Bundle(url: $0)?.infoDictionary?["CFBundleShortVersionString"] as? String } }
        let copies = others.map { app -> SingleInstance.Copy in
            // Replaced on disk since it started (an update copied over the running app): the file's version is not
            // what runs there, which is older.
            let changed = app.bundleURL.flatMap { Bundle(url: $0)?.executableURL }
                .flatMap { try? $0.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate }
            let stale = if let changed, let started = app.launchDate { changed > started } else { false }
            let first = if let a = app.launchDate, let b = me.launchDate, a != b { a < b }
                        else { app.processIdentifier < me.processIdentifier }
            return .init(version: stale ? nil : version(app.bundleURL), startedFirst: first)
        }
        switch SingleInstance.decide(own: version(Bundle.main.bundleURL), others: copies) {
        case .run:
            return false
        case .replace(let older):
            older.forEach { _ = others[$0].terminate() }
            return false
        case .handOver(let index):
            handingOver = others[index]
            let forward = { (request: SingleInstance.Request) in
                DistributedNotificationCenter.default().postNotificationName(
                    SingleInstance.open, object: request.text, userInfo: nil, deliverImmediately: true)
            }
            notifier.onOpen = { url in
                forward(.notes(url?.path ?? ""))
                NSApp.terminate(nil)
            }
            notifier.start()
            _ = others[index].activate()
            if args.contains("--show-notes") { forward(.notes("")) }
            if args.contains("--show-settings") { forward(.settings) }
            if args.contains("--show-setup") { forward(.setup) }
            return true
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        if let other = handingOver {
            // A click on a notification that started this copy arrives after launch, late when memory is short:
            // wait longer for it. Then quit, unless the other copy has gone meanwhile (it was quitting when this
            // one started): then this copy is the app.
            let clicked = notification.userInfo?[NSApplication.launchUserNotificationUserInfoKey] != nil
            DispatchQueue.main.asyncAfter(deadline: .now() + (clicked ? 10 : 2)) { [weak self] in
                guard other.isTerminated, let self else { NSApp.terminate(nil); return }
                self.handingOver = nil
                NSApp.setActivationPolicy(.regular)                 // it is the app after all: back in the Dock
                self.takeRequests()
                self.startUp()
            }
            return
        }
        startUp()
    }

    private func startUp() {
        if NotesSnapshot.run(CommandLine.arguments) { exit(0) }
        NotificationCenter.default.addObserver(forName: .languageChanged, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.retitleWindows() }
        }
        if CommandLine.arguments.contains("--settings-dump") {       // test: the window's load path, printed
            settings.load()
            DispatchQueue.main.asyncAfter(deadline: .now() + 8) { [settings] in
                print("name=\(settings.name) aliases=\(settings.aliases) language=\(settings.language) mic=\(settings.micDevice)",
                      "mics=\(settings.microphones) detail=\(settings.detail) answer_model=\(settings.answerModel)",
                      "provider=\(settings.provider) base_url=\(settings.baseURL) model=\(settings.modelName) key_saved=\(settings.apiKeySaved) screen=\(settings.screenTarget)",
                      "notify=\(settings.notifyTypes.sorted()) retention=\(settings.retention.sorted { $0.key < $1.key })",
                      "status=\(settings.status)")
                exit(0)
            }
            return
        }
        engine.onNewMeeting = { [weak self] url in self?.showNotes(url) }
        engine.onPoll = { [weak self] in
            guard let self else { return }
            self.notifier.check(self.engine.stateDir.appendingPathComponent("notifications"))
        }
        engine.start()
        L.language = engine.status.language.isEmpty ? L.language : engine.status.language
        if CommandLine.arguments.contains("--show-notes") { showNotes() }
        if CommandLine.arguments.contains("--show-settings") { showSettings() }
        // First launch, nothing installed yet, or a newer engine in this app: start with the setup guide.
        if CommandLine.arguments.contains("--show-setup") || !engine.installed || engine.engineOutdated
            || !UserDefaults.standard.bool(forKey: "setupShown") { showSetup() }
        DispatchQueue.main.async { [weak self] in self?.removeOldCopies() }
    }

    /// Copies of the app an update left behind (OldCopies): moved to the Trash (not deleted: they can be put back),
    /// taken out of Launch Services so notifications stop using their name and icon, and the user is told.
    private func removeOldCopies() {
        guard let id = Bundle.main.bundleIdentifier else { return }
        let fm = FileManager.default
        let version = { (url: URL) in Bundle(url: url)?.infoDictionary?["CFBundleShortVersionString"] as? String }
        var urls = NSWorkspace.shared.urlsForApplications(withBundleIdentifier: id)
        for dir in ["/Applications", fm.homeDirectoryForCurrentUser.appendingPathComponent("Applications").path] {
            let old = URL(fileURLWithPath: dir).appendingPathComponent(OldCopies.oldName)
            if Bundle(url: old)?.bundleIdentifier == id { urls.append(old) }      // another app of that name stays
        }
        let old = OldCopies.toRemove(found: urls.map { (path: $0.path, version: version($0)) },
                                     ownPath: Bundle.main.bundlePath, ownVersion: version(Bundle.main.bundleURL))
        guard !old.isEmpty else { return }
        NSWorkspace.shared.recycle(old.map { URL(fileURLWithPath: $0) }) { [weak self] moved, error in
            let lsregister = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
            for url in moved.values {
                let unregister = Process()
                unregister.executableURL = URL(fileURLWithPath: lsregister)
                unregister.arguments = ["-u", url.path]
                try? unregister.run()
                unregister.waitUntilExit()
            }
            let movedPaths = moved.keys.map(\.path).sorted()
            DispatchQueue.main.async {
                if !movedPaths.isEmpty {
                    self?.notifier.show(AppNotification(title: L.t("old_copies_moved"),
                                                        text: movedPaths.joined(separator: "\n"), sound: "", open: ""))
                }
                guard let error else { return }
                let failed = NSAlert()
                failed.messageText = L.t("old_copies_failed")
                failed.informativeText = error.localizedDescription + "\n\n"
                    + old.filter { !movedPaths.contains($0) }.joined(separator: "\n")
                failed.runModal()
            }
        }
    }

    func showNotes() { showNotes(engine.currentNotes) }

    func showNotes(_ url: URL?) {
        if !engine.status.language.isEmpty { L.language = engine.status.language }
        notes.follow(url)
        if window == nil {
            let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 560, height: 720),
                             styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
            w.title = L.t("notes_title")
            w.isReleasedWhenClosed = false
            w.delegate = self
            w.contentView = NSHostingView(rootView: NotesView(
                model: notes, openSpeakers: { [weak self] in self?.showSpeakers() },
                recent: { [weak self] in self?.engine.recentNotes() ?? [] },
                open: { [weak self] url in self?.showNotes(url ?? self?.engine.currentNotes) }))
            w.setFrameAutosaveName("LiveNotes")
            if w.frame.origin == .zero { w.center() }
            window = w
        }
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }
}

struct MenuIcon: View {
    @ObservedObject var engine: Engine

    var body: some View {
        if !engine.reachable || !engine.status.problems.isEmpty || engine.outdated {
            Image(systemName: "exclamationmark.triangle")
        } else if engine.status.recording {
            Image(systemName: "record.circle.fill").symbolRenderingMode(.palette).foregroundStyle(.red)
        } else if engine.status.paused {
            Image(systemName: "pause.circle")
        } else {
            Image(systemName: "waveform")
        }
    }
}

struct MenuContent: View {
    @ObservedObject var engine: Engine
    let openNotes: () -> Void
    let openNotesFile: (URL) -> Void
    let openSettings: () -> Void
    let openSetup: () -> Void
    let openPeople: () -> Void

    var body: some View {
        let s = engine.status
        VStack(alignment: .leading, spacing: 10) {
            statusLine
            ForEach(s.problems, id: \.self) { p in
                Label(p, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
            }
            if engine.outdated {
                // The background program still runs the engine of an older app until it is updated.
                Button { openSetup() } label: {
                    Label(L.t("engine_outdated"), systemImage: "arrow.down.circle.fill")
                        .font(.caption).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
                }
            }
            Divider()
            Button(L.t("open_notes"), systemImage: "note.text") { openNotes() }
            recentMeetings
            if s.recording {
                Button(L.t("stop"), systemImage: "stop.circle") { engine.send("stop") }
                // Call: the microphone is the user. Room: everyone in the room (an in-person meeting, a phone on
                // speaker). The engine picks one; the user can switch.
                Button(L.t(s.mode == "room" ? "mode_to_call" : "mode_to_room"),
                       systemImage: s.mode == "room" ? "phone" : "person.3") {
                    engine.send("mode", ["mode": s.mode == "room" ? "call" : "room"])
                }
            } else {
                Button(L.t("start"), systemImage: "record.circle") { engine.send("start") }
            }
            Button(s.paused ? L.t("resume_auto") : L.t("pause_auto"), systemImage: s.paused ? "play.circle" : "pause.circle") {
                engine.send(s.paused ? "resume_auto" : "pause_auto")
            }
            Button(L.t("meetings_folder"), systemImage: "folder") { engine.openMeetingsFolder() }
            Button(L.t("settings"), systemImage: "gearshape") { openSettings() }
            Button(L.t("people_menu"), systemImage: "person.crop.circle") { openPeople() }
            Button(L.t("setup_menu"), systemImage: "checklist") { openSetup() }
            Divider()
            Button(L.t("quit"), systemImage: "power") { NSApp.terminate(nil) }
        }
        .buttonStyle(.plain)
        .padding(14)
        .frame(width: 300)
    }

    /// Earlier meetings' notes, opened in the notes window (which otherwise shows only the latest meeting).
    private var recentMeetings: some View {
        let recent = engine.recentNotes()
        return Menu {
            if recent.isEmpty {
                Text(L.t("no_recent_meetings"))
            }
            ForEach(recent) { note in
                Button(note.title) { openNotesFile(note.url) }
            }
        } label: {
            Label(L.t("recent_meetings"), systemImage: "clock.arrow.circlepath")
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.visible)
        .fixedSize()
    }

    @ViewBuilder private var statusLine: some View {
        let s = engine.status
        if !engine.reachable {
            Label(L.t("offline"), systemImage: "bolt.horizontal.circle").foregroundStyle(.secondary)
        } else if s.recording {
            VStack(alignment: .leading, spacing: 2) {
                Label(L.t("recording"), systemImage: "record.circle.fill").foregroundStyle(.red).font(.headline)
                Text(s.title).font(.callout)
                if !s.mode.isEmpty {
                    Text(L.t(s.mode == "room" ? "mode_room" : "mode_call")).font(.caption).foregroundStyle(.secondary)
                }
                TimelineView(.periodic(from: .now, by: 1)) { ctx in
                    Text(elapsed(since: s.started_at, now: ctx.date)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
            }
        } else if s.processing {
            Label(L.t("processing"), systemImage: "hourglass")
        } else if s.paused {
            Label(L.t("paused"), systemImage: "pause.circle")
        } else {
            Label(L.t("idle"), systemImage: "waveform").foregroundStyle(.secondary)
        }
    }

    private func elapsed(since start: Double, now: Date) -> String {
        guard start > 0 else { return "" }
        let secs = max(0, Int(now.timeIntervalSince1970 - start))
        return String(format: "%d:%02d:%02d", secs / 3600, secs / 60 % 60, secs % 60)
    }
}
