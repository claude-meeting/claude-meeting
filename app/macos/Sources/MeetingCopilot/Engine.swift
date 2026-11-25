import AppKit
import Foundation
import MeetingCopilotCore

extension Notification.Name {
    static let languageChanged = Notification.Name("MeetingCopilot.languageChanged")
}

/// Talks to the background program through files: reads its status, writes commands, and keeps a
/// heartbeat so it leaves the live notes to this app instead of opening Terminal.
@MainActor
final class Engine: ObservableObject {
    @Published private(set) var status = EngineStatus()
    @Published private(set) var reachable = false
    /// engineOutdated, looked at every few seconds (it reads files): what the menu shows.
    @Published private(set) var outdated = false
    private var outdatedCheckedAt = Date.distantPast
    /// A newer copy of the app was installed over this running one: reopen to run it.
    @Published private(set) var appReplaced = false

    @Published private(set) var appDir: URL
    private var timer: Timer?
    private var lastNotes = ""
    var onNewMeeting: ((URL) -> Void)?
    var onPoll: (() -> Void)?

    /// Where the app installs its own copy of the engine (first-run setup).
    static let supportEngine = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/Meeting Copilot/engine")

    init() { appDir = Engine.locate() }

    /// The engine's app/ folder: the one the background service runs (from its LaunchAgent), else the
    /// one chosen at setup, else an installed copy (the app's own, or a git checkout in
    /// ~/meeting-copilot), else where the app's own copy will go.
    static func locate() -> URL {
        // For tests and development: this engine, whatever is installed (as MEETING_COPILOT_CONFIG names a config).
        if let dir = ProcessInfo.processInfo.environment["MEETING_COPILOT_ENGINE"], !dir.isEmpty {
            return URL(fileURLWithPath: dir)
        }
        var candidates: [URL] = []
        let plist = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents/com.meeting-copilot.autopilot.plist")
        if let data = try? Data(contentsOf: plist),
           let dict = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
           let args = dict["ProgramArguments"] as? [String], args.count > 1 {
            candidates.append(URL(fileURLWithPath: args[1]).deletingLastPathComponent().deletingLastPathComponent())
        }
        if let configured = UserDefaults.standard.string(forKey: "engineDir") {
            candidates.append(URL(fileURLWithPath: configured))
        }
        candidates += [supportEngine.appendingPathComponent("app"),
                       URL(fileURLWithPath: NSString(string: "~/meeting-copilot/app").expandingTildeInPath)]
        return candidates.first { isInstalled($0) } ?? supportEngine.appendingPathComponent("app")
    }

    static func isInstalled(_ dir: URL) -> Bool {
        let fm = FileManager.default
        guard fm.isExecutableFile(atPath: dir.appendingPathComponent("asr-venv/bin/python").path),
              let models = try? fm.contentsOfDirectory(atPath: dir.appendingPathComponent("models/speaker").path)
        else { return false }
        return models.contains { name in
            name.hasSuffix(".onnx")
                && ((try? fm.attributesOfItem(atPath: dir.appendingPathComponent("models/speaker/\(name)").path)[.size]
                     as? Int) ?? 0) > 0
        }
    }

    /// The engine code version installed in Application Support (engine/.version), written by setup.
    static var installedVersion: String? {
        try? String(contentsOf: supportEngine.appendingPathComponent(".version"), encoding: .utf8)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// The engine build this app carries: commit count plus hash (and -dirty), from build.sh.
    static var bundleVersion: String {
        (Bundle.main.infoDictionary?["MCEngineVersion"] ?? Bundle.main.infoDictionary?["CFBundleVersion"]) as? String ?? "dev"
    }

    /// True when the app carries a newer engine than the copy in Application Support.
    var engineOutdated: Bool {
        appDir == Engine.supportEngine.appendingPathComponent("app") && installed
            && Engine.installedVersion != Engine.bundleVersion
    }

    var installed: Bool { Engine.isInstalled(appDir) }
    private var lastEngineLanguage = ""

    func use(_ dir: URL) {
        UserDefaults.standard.set(dir.path, forKey: "engineDir")
        appDir = dir
        poll()
    }

    var stateDir: URL { appDir.appendingPathComponent("state") }

    func start() {
        poll()
        let t = Timer(timeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.poll() }
        }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    private func poll() {
        if Date().timeIntervalSince(outdatedCheckedAt) >= 10 {
            outdatedCheckedAt = Date()
            let now = engineOutdated
            if now != outdated { outdated = now }
            // Bundle.main's Info.plist is read at launch; the file is the one installed now.
            let replaced = SingleInstance.replaced(executableChanged: SingleInstance.executableChanged(Bundle.main.bundleURL),
                                                   launched: NSRunningApplication.current.launchDate,
                                                   running: SingleInstance.version(Bundle.main.infoDictionary),
                                                   installed: SingleInstance.installedVersion(Bundle.main.bundleURL))
            if replaced != appReplaced { appReplaced = replaced }
        }
        guard installed else {
            if reachable { reachable = false }
            return
        }
        try? FileManager.default.createDirectory(at: stateDir, withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: stateDir.appendingPathComponent("app.heartbeat").path, contents: nil)
        onPoll?()
        guard let data = try? Data(contentsOf: stateDir.appendingPathComponent("status.json")),
              let s = try? JSONDecoder().decode(EngineStatus.self, from: data) else {
            if reachable { reachable = false }
            return
        }
        // The background program may spend up to a minute in one Minutes call; that is not "offline".
        let alive = Date().timeIntervalSince1970 - s.updated < 90
        if alive != reachable { reachable = alive }
        if !s.language.isEmpty, s.language != lastEngineLanguage {
            lastEngineLanguage = s.language          // a change the engine made, not Settings' own (undone otherwise)
            if s.language != L.language {
                L.language = s.language
                NotificationCenter.default.post(name: .languageChanged, object: nil)
            }
        }
        if s != status { status = s }
        if s.recording, !s.notes.isEmpty, s.notes != lastNotes {
            lastNotes = s.notes
            if s.open_viewer { onNewMeeting?(URL(fileURLWithPath: s.notes)) }
        }
    }

    /// Ask the background program to restart once it is idle (settings changed). launchd starts it again.
    func restartWhenIdle() { send("restart_when_idle") }

    /// Stop the background program now, e.g. before its files are replaced. Returns false if none ran.
    func stopService() async -> Bool {
        await Shell.runStatus("/bin/launchctl", ["bootout", "gui/\(getuid())/com.meeting-copilot.autopilot"]).code == 0
    }

    static func markInstalled() {
        try? bundleVersion.write(to: supportEngine.appendingPathComponent(".version"), atomically: true, encoding: .utf8)
    }

    /// Open the installed copy: it replaces this older one (SingleInstance.decide).
    func reopen() {
        let config = NSWorkspace.OpenConfiguration()
        config.createsNewApplicationInstance = true
        NSWorkspace.shared.openApplication(at: Bundle.main.bundleURL, configuration: config) { _, _ in }
    }

    func send(_ cmd: String, _ extra: [String: String] = [:]) {
        let dir = stateDir.appendingPathComponent("commands")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let name = "\(Int(Date().timeIntervalSince1970 * 1000))-\(cmd).json"
        var body = extra
        body["cmd"] = cmd
        guard let data = try? JSONSerialization.data(withJSONObject: body) else { return }
        try? data.write(to: dir.appendingPathComponent(name), options: .atomic)
    }

    /// The live notes of the meeting in progress, or the latest one.
    /// Where the engine writes the live notes of each meeting.
    var liveNotesDir: URL {
        URL(fileURLWithPath: status.meetings_dir.isEmpty
            ? NSString(string: "~/meetings").expandingTildeInPath : status.meetings_dir)
            .appendingPathComponent("live")
    }

    /// Earlier meetings' notes, newest first, for the menu.
    func recentNotes() -> [RecentNote] { RecentNotes.list(in: liveNotesDir) }

    var currentNotes: URL? {
        if !status.notes.isEmpty { return URL(fileURLWithPath: status.notes) }
        let current = liveNotesDir.appendingPathComponent("current.md")
        return FileManager.default.fileExists(atPath: current.path) ? current.resolvingSymlinksInPath() : nil
    }

    func openMeetingsFolder() {
        let dir = status.meetings_dir.isEmpty ? NSString(string: "~/meetings").expandingTildeInPath : status.meetings_dir
        NSWorkspace.shared.open(URL(fileURLWithPath: dir))
    }
}
