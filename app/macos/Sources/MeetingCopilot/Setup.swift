import AVFoundation
import ServiceManagement
import SwiftUI
import MeetingCopilotCore

/// First-run setup: every step shows whether it is done and what to do if not.
@MainActor
final class SetupModel: ObservableObject {
    enum State { case ok, todo, working, unknown }

    @Published var minutes: State = .unknown
    @Published var minutesIssues: [String] = []
    @Published var claude: State = .unknown
    @Published var claudeInstalled = false
    @Published var provider = "claude"     // [llm] provider: "openai" is another service in place of Claude
    @Published var otherModelReady = false
    @Published var engineState: State = .unknown
    @Published var screen: State = .unknown
    @Published var voice: State = .unknown
    @Published var loginItem = false
    @Published var name = ""
    @Published var installLog: [String] = []
    @Published var installProgress = 0.0
    @Published var voiceMessage = ""
    @Published var recordingVoice = false
    @Published var minutesInstalling = false
    @Published var claudeInstalling = false
    @Published var prereqMessage = ""
    @Published var loginMessage = ""
    private var refreshing = false

    let engine: Engine
    private var timer: Timer?
    private var recorder: AVAudioRecorder?

    init(engine: Engine) {
        self.engine = engine
        name = UserConfig.string("user", "name") ?? NSFullUserName().components(separatedBy: " ").first ?? ""
    }

    /// The app carries a newer engine than the installed copy: the Install button becomes Update.
    var engineUpdate: Bool { engine.engineOutdated }
    /// Never replace the engine's files or restart it while it records or wraps up a meeting.
    var engineBusy: Bool { engine.status.recording || engine.status.processing }

    var modelReady: Bool { provider == "openai" ? otherModelReady : claude == .ok }
    var allDone: Bool { SetupRules.allDone(minutes: minutes == .ok, model: modelReady, engine: engineState == .ok) }
    /// Why Install / Update is disabled (nil: it is enabled); see SetupRules.
    var installBlocked: String? { SetupRules.installBlocked(name: name, minutesReady: minutes == .ok, engineBusy: engineBusy) }

    static var minutesApp: String { UserConfig.path("paths", "minutes_app", default: "/Applications/Minutes.app") }
    static var voiceprintFile: String {
        UserConfig.path("voice", "voiceprint_file", default: "~/.config/meeting-copilot/voiceprint.json")
    }

    func startChecking() {
        refresh(deep: true)
        checkScreen()
        timer?.invalidate()
        let t = Timer(timeInterval: 3, repeats: true) { [weak self] _ in      // cheap file checks only
            Task { @MainActor in self?.refresh() }
        }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    func stopChecking() { timer?.invalidate(); timer = nil }

    /// Local checks every time; `deep` also asks Claude Code and Minutes (slow, so only on open,
    /// after an action, or on "Check again"), never two at once.
    func refresh(deep: Bool = false) {
        let fm = FileManager.default
        minutes = fm.fileExists(atPath: Self.minutesApp) ? .ok : .todo
        if engineState != .working {
            engineState = SetupRules.engineDone(installed: engine.installed, outdated: engine.engineOutdated,
                                                asrMissing: engine.status.asr_missing) ? .ok : .todo
        }
        voice = fm.fileExists(atPath: Self.voiceprintFile) ? .ok : .todo
        loginItem = SMAppService.mainApp.status == .enabled
        claudeInstalled = Self.claudePath() != nil
        if !claudeInstalled { claude = .todo }
        provider = UserConfig.string("llm", "provider") ?? "claude"
        otherModelReady = SetupRules.modelReady(provider: provider, claudeSignedIn: false,
                                                baseURL: UserConfig.string("llm", "base_url"),
                                                model: UserConfig.string("llm", "model"))
        guard deep, !refreshing else { return }
        refreshing = true
        Task {
            if let claudePath = Self.claudePath() {
                let out = await Shell.run(claudePath, ["auth", "status"], timeout: 30)
                claude = out.range(of: #""loggedIn": ?true"#, options: .regularExpression) != nil ? .ok : .todo
            }
            if minutes == .ok {
                let out = await Shell.run(Self.minutesApp + "/Contents/MacOS/minutes", ["health", "--json"], timeout: 60)
                if let data = out.data(using: .utf8),
                   let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   let items = (root["data"] as? [String: Any])?["items"] as? [[String: Any]] {
                    minutesIssues = items.filter { ($0["optional"] as? Bool) == false && ($0["state"] as? String) != "ready" }
                        .compactMap { "\($0["label"] ?? ""): \($0["detail"] ?? "")" }
                }
            }
            refreshing = false
        }
    }

    static func claudePath() -> String? {
        let candidates = [NSString(string: "~/.local/bin/claude").expandingTildeInPath, "/opt/homebrew/bin/claude",
                          "/usr/local/bin/claude"]
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    // MARK: actions

    func open(_ url: String) { NSWorkspace.shared.open(URL(string: url)!) }

    /// The engine's installer: the installed engine's, else the one bundled in the app.
    private var installer: String? {
        let own = engine.appDir.deletingLastPathComponent().appendingPathComponent("install.sh").path
        if FileManager.default.fileExists(atPath: own) { return own }
        let bundled = Bundle.main.resourceURL?.appendingPathComponent("engine/install.sh").path
        return bundled.flatMap { FileManager.default.fileExists(atPath: $0) ? $0 : nil }
    }

    /// Download and install Minutes (checked against its developer signature) or Claude Code
    /// (Anthropic's installer), through the engine installer's --get-* steps.
    func installPrerequisite(_ which: String) {
        guard let installer else { installLog.append(L.t("setup_no_engine")); return }
        if which == "minutes" { minutesInstalling = true } else { claudeInstalling = true }
        prereqMessage = ""
        Task {
            // --only-prerequisites: the installer does the download and stops; it never writes into the
            // engine folder, so the copy inside the app bundle (read-only) can be used.
            let r = await Shell.runStatus("/bin/bash", [installer, "--get-\(which)", "--only-prerequisites"], timeout: 900)
            if which == "minutes" { minutesInstalling = false } else { claudeInstalling = false }
            if r.code == 0 {
                refresh(deep: true)
            } else {
                let lines = (r.out + "\n" + r.err).split(separator: "\n").map(String.init).filter { !$0.isEmpty }
                prereqMessage = lines.last ?? L.t("setup_failed")
            }
        }
    }

    func claudeLogin() {
        let claude = Self.claudePath() ?? "claude"
        let script = "on run argv\n tell application \"Terminal\"\n  do script (quoted form of item 1 of argv) & \" auth login\"\n  activate\n end tell\nend run"
        Task { _ = await Shell.run("/usr/bin/osascript", ["-e", script, claude]) }
    }

    func openPrivacy(_ pane: String) {
        open("x-apple.systempreferences:com.apple.preference.security?\(pane)")
    }

    /// Ask the screen-capture helper itself (launched on its own, so macOS checks its permission).
    /// `request` also shows the system prompt / adds the helper to the Screen Recording list.
    func checkScreen(request: Bool = false) {
        let helper = engine.appDir.appendingPathComponent("bin/MeetingCopilotScreen.app")
        guard FileManager.default.fileExists(atPath: helper.path) else { screen = .unknown; return }
        let out = FileManager.default.temporaryDirectory.appendingPathComponent("mc-screen-access")
        try? FileManager.default.removeItem(at: out)
        Task {
            var args = ["-g", "-n", "-a", helper.path, "--args", "--access-status", out.path]
            if request { args.append("--request") }
            _ = await Shell.run("/usr/bin/open", args)
            for _ in 0..<20 {
                try? await Task.sleep(nanoseconds: 250_000_000)
                if let s = try? String(contentsOf: out, encoding: .utf8) {
                    screen = s.hasPrefix("granted") ? .ok : .todo
                    return
                }
            }
        }
    }

    /// Copy the engine bundled in the app (unless one is installed) and run its installer, showing progress.
    func install() {
        guard !engineBusy else { installLog = [L.t("setup_busy")]; return }
        engineState = .working
        installLog = []
        installProgress = 0
        Task {
            let fm = FileManager.default
            var appDir = engine.appDir
            let own = Engine.supportEngine.appendingPathComponent("app")
            let fresh = !fm.fileExists(atPath: appDir.appendingPathComponent("lib").path)
            let updating = fresh || (appDir == own && Engine.installedVersion != Engine.bundleVersion)
            if updating {
                guard let bundled = Bundle.main.resourceURL?.appendingPathComponent("engine"),
                      fm.fileExists(atPath: bundled.path) else {
                    installLog.append(L.t("setup_no_engine")); engineState = .todo; return
                }
                installLog.append(fresh ? L.t("setup_copying") : L.t("setup_updating"))
                if !fresh { _ = await engine.stopService() }   // not while it runs from these files
                let r = await Shell.runStatus("/bin/bash", [bundled.appendingPathComponent("sync-engine.sh").path,
                                                             Engine.supportEngine.path, Engine.bundleVersion], timeout: 300)
                guard r.code == 0 else {
                    installLog.append(r.err.split(separator: "\n").last.map(String.init) ?? L.t("setup_failed"))
                    engineState = .todo; return
                }
                appDir = own
            }
            let installer = appDir.deletingLastPathComponent().appendingPathComponent("install.sh").path
            let steps = 8.0
            let ok = await Shell.stream("/bin/bash", [installer, "--name", name]) { line in
                Task { @MainActor in
                    self.installLog.append(line)
                    if line.hasPrefix("==>") { self.installProgress = min(1, self.installProgress + 1 / steps) }
                }
            }
            if ok && Engine.isInstalled(appDir) {
                if appDir == own { Engine.markInstalled() }   // only now does the copy count as current
                engine.use(appDir)
                installProgress = 1
                engineState = .ok
                checkScreen()
            } else {
                engineState = .todo
            }
            refresh(deep: true)
        }
    }

    /// Record 30 s of the user talking and enroll it as their voiceprint.
    func recordVoice() {
        AVCaptureDevice.requestAccess(for: .audio) { granted in
            Task { @MainActor in
                guard granted else { self.voiceMessage = L.t("setup_mic_denied"); return }
                self.startRecording()
            }
        }
    }

    private func startRecording() {
        let url = Engine.supportEngine.deletingLastPathComponent().appendingPathComponent("enroll.wav")
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let settings: [String: Any] = [AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: 16000,
                                       AVNumberOfChannelsKey: 1, AVLinearPCMBitDepthKey: 16,
                                       AVLinearPCMIsFloatKey: false]
        guard let r = try? AVAudioRecorder(url: url, settings: settings), r.record(forDuration: 30) else {
            voiceMessage = L.t("setup_record_failed"); return
        }
        recorder = r
        recordingVoice = true
        voiceMessage = L.t("setup_recording")
        Task {
            try? await Task.sleep(nanoseconds: 31_000_000_000)
            recordingVoice = false
            voiceMessage = L.t("setup_enrolling")
            let r = await Shell.runStatus(engine.appDir.appendingPathComponent("asr-venv/bin/python").path,
                                          [engine.appDir.appendingPathComponent("tools/enroll_voiceprint.py").path, url.path])
            let out = (r.out + "\n" + r.err).trimmingCharacters(in: .whitespacesAndNewlines)   // errors arrive on stderr
            voiceMessage = r.code == 0 && r.out.contains("enrolled") ? L.t("setup_enrolled")
                : L.t("setup_enroll_failed") + " " + out.suffix(160)
            refresh()
        }
    }

    func setLoginItem(_ on: Bool) {
        loginMessage = ""
        if on && Bundle.main.bundlePath.contains("/AppTranslocation/") {
            loginMessage = L.t("setup_login_translocated")       // a copy macOS made for a quarantined app
            loginItem = false
            return
        }
        do {
            if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
        } catch {
            loginMessage = error.localizedDescription
        }
        let status = SMAppService.mainApp.status
        loginItem = status == .enabled
        if on && status == .requiresApproval {
            loginMessage = L.t("setup_login_approval")
            SMAppService.openSystemSettingsLoginItems()
        }
    }
}

struct SetupView: View {
    @ObservedObject var model: SetupModel
    let openSettings: () -> Void
    let close: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 4) {
                Text(L.t("setup_title")).font(.title2.bold())
                Text(L.t("setup_intro")).font(.callout).foregroundStyle(.secondary)
            }
            .padding(18)
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    step(model.minutes, L.t("setup_minutes"), L.t("setup_minutes_detail")) {
                        if model.minutes != .ok {
                            HStack {
                                Button(L.t("setup_install_minutes")) { model.installPrerequisite("minutes") }
                                    .disabled(model.minutesInstalling)
                                if model.minutesInstalling { ProgressView().controlSize(.small) }
                                Button(L.t("setup_get_minutes")) { model.open("https://github.com/silverstein/minutes/releases") }
                            }
                        }
                        ForEach(model.minutesIssues, id: \.self) { Text($0).font(.caption).foregroundStyle(.orange) }
                        if !model.prereqMessage.isEmpty && model.minutesInstalling == false && model.minutes != .ok {
                            Text(model.prereqMessage).font(.caption).foregroundStyle(.orange)
                        }
                    }
                    if model.provider == "openai" {
                        step(model.otherModelReady ? .ok : .todo, L.t("setup_model_other"), L.t("setup_model_other_detail")) {
                            Button(L.t("setup_open_settings")) { openSettings() }.disabled(!model.engine.installed)
                            if !model.engine.installed { settingsNeedEngine }
                        }
                    } else {
                    step(model.claude, L.t("setup_claude"), L.t("setup_claude_detail")) {
                        if model.claude != .ok {
                            // Settings are read and saved with the engine's tools: not before it is installed.
                            Button(L.t("setup_other_model")) { openSettings() }.buttonStyle(.link)
                                .disabled(!model.engine.installed)
                            if !model.engine.installed { settingsNeedEngine }
                            if model.claudeInstalled {
                                HStack {
                                    Button(L.t("setup_claude_login")) { model.claudeLogin() }
                                    Button(L.t("setup_recheck")) { model.refresh(deep: true) }
                                }
                            } else {
                                HStack {
                                    Button(L.t("setup_install_claude")) { model.installPrerequisite("claude") }
                                        .disabled(model.claudeInstalling)
                                    if model.claudeInstalling { ProgressView().controlSize(.small) }
                                    Button(L.t("setup_get_claude")) { model.open("https://claude.com/claude-code") }
                                }
                                if !model.prereqMessage.isEmpty && model.claudeInstalling == false {
                                    Text(model.prereqMessage).font(.caption).foregroundStyle(.orange)
                                }
                            }
                        }
                    }
                    }
                    step(model.engineState, L.t("setup_engine"), L.t("setup_engine_detail")) {
                        if model.engineState == .todo {
                            if !model.engine.status.asr_missing.isEmpty {
                                Text(String(format: L.t("setup_model_missing"), model.engine.status.asr_missing))
                                    .font(.caption).foregroundStyle(.orange)
                            }
                            // A label of its own: the field starts filled with the Mac account's name, which hides
                            // the placeholder, and a bare name next to Install read as something else.
                            Text(L.t("s_name")).font(.caption).foregroundStyle(.secondary)
                            HStack {
                                TextField("", text: $model.name).frame(width: 220)       // the caption above names it
                                    .accessibilityLabel(L.t("s_name"))
                                Button(L.t(model.engineUpdate ? "setup_update" : "setup_install")) { model.install() }
                                    .disabled(model.installBlocked != nil)
                                    .help(model.installBlocked.map { $0.isEmpty ? "" : L.t($0) } ?? "")
                            }
                        }
                        if model.engineState == .working || !model.installLog.isEmpty {
                            ProgressView(value: model.installProgress).frame(width: 320)
                            Text(model.installLog.last ?? "").font(.caption.monospaced()).foregroundStyle(.secondary)
                                .lineLimit(1)
                        }
                    }
                    step(.unknown, L.t("setup_permissions"), L.t("setup_permissions_detail")) {
                        HStack {
                            Button(L.t("setup_open_mic")) { model.openPrivacy("Privacy_Microphone") }
                            Button(L.t("setup_open_screen")) { model.openPrivacy("Privacy_ScreenCapture") }
                        }
                    }
                    step(model.screen, L.t("setup_screen"), L.t("setup_screen_detail")) {
                        if model.screen != .ok {
                            HStack {
                                Button(L.t("setup_open_screen")) { model.openPrivacy("Privacy_ScreenCapture") }
                                Button(L.t("setup_recheck")) { model.checkScreen(request: true) }
                            }
                        }
                    }
                    step(model.voice, L.t("setup_voice"), L.t("setup_voice_detail")) {
                        HStack {
                            Button(model.voice == .ok ? L.t("setup_rerecord") : L.t("setup_record")) { model.recordVoice() }
                                .disabled(!SetupRules.voiceEnabled(engineDone: model.engineState == .ok, recording: model.recordingVoice))
                            if model.recordingVoice { ProgressView().controlSize(.small) }
                        }
                        if !model.voiceMessage.isEmpty { Text(model.voiceMessage).font(.caption).foregroundStyle(.secondary) }
                    }
                    step(model.loginItem ? .ok : .todo, L.t("setup_login"), L.t("setup_login_detail")) {
                        Toggle(L.t("setup_login_toggle"), isOn: Binding(get: { model.loginItem }, set: { model.setLoginItem($0) }))
                        if !model.loginMessage.isEmpty { Text(model.loginMessage).font(.caption).foregroundStyle(.secondary) }
                    }
                }
                .padding(18)
            }
            Divider()
            HStack {
                Text(model.allDone ? L.t("setup_ready") : L.t("setup_not_ready")).font(.callout)
                    .foregroundStyle(model.allDone ? .green : .secondary)
                Spacer()
                Button(L.t("setup_done")) { close() }.keyboardShortcut(.defaultAction)
            }
            .padding(14)
        }
        .frame(minWidth: 560, minHeight: 620)
    }

    @ViewBuilder
    private var settingsNeedEngine: some View {
        Text(L.t("setup_settings_need_engine")).font(.caption).foregroundStyle(.secondary)
    }

    private func step<Content: View>(_ state: SetupModel.State, _ title: String, _ detail: String,
                                     @ViewBuilder actions: () -> Content) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Group {
                switch state {
                case .ok: Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                case .todo: Image(systemName: "circle").foregroundStyle(.orange)
                case .working: ProgressView().controlSize(.small)
                case .unknown: Image(systemName: "info.circle").foregroundStyle(.secondary)
                }
            }
            .font(.title3).frame(width: 24)
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(.headline)
                Text(detail).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                actions()
            }
        }
    }
}
