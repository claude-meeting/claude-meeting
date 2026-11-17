import SwiftUI
import MeetingCopilotCore

/// The settings the window edits, read from and written to config.toml through
/// app/tools/config_cli.py (which keeps the file's other lines and comments).
@MainActor
final class SettingsModel: ObservableObject {
    @Published var name = ""
    @Published var aliases = ""
    @Published var language = ""
    @Published var autoRecord = true
    @Published var micDevice = ""
    @Published var microphones: [String] = []
    @Published var explainEnabled = true
    @Published var detail = "medium"
    @Published var answerModel = SettingsModel.strongModel
    // [llm]: who writes the notes and answers. "claude" is the user's subscription through Claude Code; "openai"
    // is any service that speaks the OpenAI chat format, at the address and with the model names given here.
    @Published var provider = "claude"
    @Published var baseURL = ""
    @Published var modelName = ""
    @Published var fastModel = ""
    @Published var vision = false
    @Published var apiKey = ""           // typed now; the saved key is never read back into the window
    @Published var apiKeySaved = false
    @Published var testResult = ""
    @Published var testing = false
    @Published var webVerify = true
    @Published var openViewer = true
    @Published var screenEnabled = true
    @Published var screenTarget = "all"
    @Published var sendImages = true
    @Published var notifyTypes: Set<String> = []
    @Published var retention: [String: Int] = [:]
    @Published var status = ""
    @Published var busy = false
    @Published var isLoaded = false      // a Save before a successful load would write defaults over the config

    /// [explain] answer_model: the engine's default, and the one that uses less of the Claude plan.
    static let strongModel = "claude-sonnet-5-5"
    static let fastModel = "claude-haiku-5-5"
    static let notifyKinds = ["heard_name", "question_to_me", "mention", "my_todo", "explain",
                              "decision", "deadline", "risk", "other_todo"]
    static let retentionKeys = ["audio_days", "screenshots_days", "video_days", "live_notes_days",
                                "transcripts_days", "voiceprints_days"]
    private var loaded: [String: AnyHashable] = [:]
    let engine: Engine

    init(engine: Engine) { self.engine = engine }

    private var python: String { engine.appDir.appendingPathComponent("asr-venv/bin/python").path }
    private var cli: String { engine.appDir.appendingPathComponent("tools/config_cli.py").path }

    func load() {
        guard !busy else { return }               // reopened mid-save: the save finishes first
        busy = true
        Task {
            let out = await run([cli, "get"])
            busy = false
            // The tool's JSON is the last line that starts with "{" (warnings may precede it).
            let jsonLine = out.split(separator: "\n").last { $0.hasPrefix("{") }.map(String.init) ?? out
            guard let data = jsonLine.data(using: .utf8),
                  let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let cfg = root["config"] as? [String: Any] else {
                status = L.t("settings_load_failed"); return
            }
            func sec(_ s: String) -> [String: Any] { cfg[s] as? [String: Any] ?? [:] }
            name = sec("user")["name"] as? String ?? ""
            aliases = (sec("user")["aliases"] as? [String] ?? []).joined(separator: ", ")
            language = sec("user")["language"] as? String ?? ""
            autoRecord = sec("autopilot")["auto_record"] as? Bool ?? true
            micDevice = sec("audio")["mic_device"] as? String ?? ""
            microphones = root["microphones"] as? [String] ?? []
            explainEnabled = sec("explain")["enabled"] as? Bool ?? true
            detail = sec("explain")["detail"] as? String ?? "medium"
            answerModel = sec("explain")["answer_model"] as? String ?? Self.strongModel
            provider = sec("llm")["provider"] as? String ?? "claude"
            baseURL = sec("llm")["base_url"] as? String ?? ""
            modelName = sec("llm")["model"] as? String ?? ""
            fastModel = sec("llm")["fast_model"] as? String ?? ""
            vision = sec("llm")["vision"] as? Bool ?? false
            apiKey = ""
            apiKeySaved = root["api_key_set"] as? Bool ?? false
            webVerify = sec("explain")["web_verify"] as? Bool ?? true
            openViewer = sec("copilot")["open_viewer"] as? Bool ?? true
            screenEnabled = sec("screen")["enabled"] as? Bool ?? true
            screenTarget = sec("screen")["target"] as? String ?? "all"
            sendImages = sec("screen")["send_images"] as? Bool ?? true
            notifyTypes = Set(sec("notify")["types"] as? [String] ?? [])
            retention = Dictionary(uniqueKeysWithValues: Self.retentionKeys.map { ($0, sec("retention")[$0] as? Int ?? 0) })
            loaded = current
            isLoaded = true
            status = ""
        }
    }

    private var current: [String: AnyHashable] {
        var v: [String: AnyHashable] = [
            "user.name": name,
            "user.aliases": aliases.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty },
            "user.language": language, "autopilot.auto_record": autoRecord, "audio.mic_device": micDevice,
            "explain.enabled": explainEnabled, "explain.detail": detail, "explain.web_verify": webVerify,
            "explain.answer_model": answerModel,
            "llm.provider": provider, "llm.base_url": baseURL.trimmingCharacters(in: .whitespaces),
            "llm.model": modelName.trimmingCharacters(in: .whitespaces),
            "llm.fast_model": fastModel.trimmingCharacters(in: .whitespaces), "llm.vision": vision,
            "copilot.open_viewer": openViewer, "screen.enabled": screenEnabled, "screen.target": screenTarget,
            "screen.send_images": sendImages,
            "notify.types": Self.notifyKinds.filter { notifyTypes.contains($0) },
        ]
        for (k, days) in retention { v["retention.\(k)"] = days }
        return v
    }

    func save() {
        guard isLoaded, !busy else { return }
        let changes = current.filter { loaded[$0.key] != $0.value }
        let key = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !changes.isEmpty || !key.isEmpty else { status = L.t("settings_no_change"); return }
        guard let data = try? JSONSerialization.data(withJSONObject: changes.mapValues { $0.base }),
              let json = String(data: data, encoding: .utf8) else { return }
        busy = true
        Task {
            var out = "\"ok\": true"
            if !key.isEmpty {                              // on stdin: arguments can be listed by other programs
                let r = await Shell.runStatus(python, [cli, "set-key"], timeout: 30, input: key)
                out = r.code == 0 ? r.out : r.out + "\n" + r.err
                if out.contains("\"ok\": true") { apiKey = ""; apiKeySaved = true }
            }
            if out.contains("\"ok\": true"), !changes.isEmpty { out = await run([cli, "set", json]) }
            if out.contains("\"ok\": true") {
                loaded = current
                if let lang = changes["user.language"] as? String {
                    L.language = L.family(lang.isEmpty ? (Locale.preferredLanguages.first ?? "") : lang)
                    NotificationCenter.default.post(name: .languageChanged, object: nil)
                }
                engine.restartWhenIdle()               // the service restarts itself once nothing is in flight
                status = L.t(engine.status.recording || engine.status.processing ? "settings_saved_later" : "settings_saved")
            } else {
                status = L.t("settings_save_failed") + " " + out.suffix(200)
            }
            busy = false
        }
    }

    /// One short question to the model, with what the window shows now (saved or not): does the address answer,
    /// is the key taken, is there such a model?
    func test() {
        guard !testing else { return }
        let typed: [String: Any] = ["provider": provider, "base_url": baseURL.trimmingCharacters(in: .whitespaces),
                                    "model": modelName.trimmingCharacters(in: .whitespaces),
                                    "fast_model": fastModel.trimmingCharacters(in: .whitespaces), "vision": vision]
        guard let data = try? JSONSerialization.data(withJSONObject: typed), let json = String(data: data, encoding: .utf8)
        else { return }
        let key = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        testing = true
        testResult = ""
        Task {
            let r = await Shell.runStatus(python, [cli, "llm-test", json] + (key.isEmpty ? [] : ["--key-stdin"]),
                                          timeout: 90, input: key.isEmpty ? nil : key)
            let line = r.out.split(separator: "\n").last { $0.hasPrefix("{") }.map(String.init) ?? ""
            let reply = (try? JSONSerialization.jsonObject(with: Data(line.utf8))) as? [String: Any] ?? [:]
            if reply["ok"] as? Bool == true {
                testResult = L.t("s_test_ok") + (reply["reply"] as? String ?? "")
            } else {
                let why = reply["error"] as? String ?? String((r.err.isEmpty ? r.out : r.err).suffix(200))
                let known = ["no_url", "no_model", "rejected", "missing", "signed_out"].contains(why)
                let which = (reply["model"] as? String).map { known ? "" : "\($0) " } ?? ""    // which model failed
                testResult = L.t("s_test_failed") + which + (known ? L.t("s_test_" + why) : why)
            }
            testing = false
        }
    }

    private func run(_ args: [String]) async -> String {
        let r = await Shell.runStatus(python, args, timeout: 60)
        return r.code == 0 ? r.out : r.out + "\n" + r.err
    }
}

struct SettingsView: View {
    @ObservedObject var model: SettingsModel

    var body: some View {
        VStack(spacing: 0) {
            Form {
                Section(L.t("s_you")) {
                    TextField(L.t("s_name"), text: $model.name)
                    TextField(L.t("s_aliases"), text: $model.aliases)
                    Picker(L.t("s_language"), selection: $model.language) {
                        Text(L.t("s_language_system")).tag("")
                        Text("繁體中文").tag("zh-TW")
                        Text("简体中文").tag("zh-CN")
                        Text("English").tag("en")
                        if !["", "zh-TW", "zh-CN", "en"].contains(model.language) { Text(model.language).tag(model.language) }
                    }
                }
                Section(L.t("s_recording")) {
                    Toggle(L.t("s_auto_record"), isOn: $model.autoRecord)
                    Picker(L.t("s_microphone"), selection: $model.micDevice) {
                        Text(L.t("s_mic_default")).tag("")
                        ForEach(model.microphones, id: \.self) { Text($0).tag($0) }
                        if !model.micDevice.isEmpty && !model.microphones.contains(model.micDevice) {
                            Text(model.micDevice).tag(model.micDevice)
                        }
                    }
                }
                Section(L.t("s_model")) {
                    Picker(L.t("s_provider"), selection: $model.provider) {
                        Text(L.t("s_provider_claude")).tag("claude")
                        Text(L.t("s_provider_other")).tag("openai")
                    }
                    if model.provider == "openai" {
                        TextField(L.t("s_base_url"), text: $model.baseURL, prompt: Text(verbatim: "https://api.deepseek.com/v1"))
                        TextField(L.t("s_model_name"), text: $model.modelName, prompt: Text(verbatim: "deepseek-chat"))
                        TextField(L.t("s_fast_model"), text: $model.fastModel, prompt: Text(L.t("s_fast_model_hint")))
                        SecureField(L.t("s_api_key"), text: $model.apiKey,
                                    prompt: Text(L.t(model.apiKeySaved ? "s_api_key_saved" : "s_api_key_none")))
                        Toggle(L.t("s_vision"), isOn: $model.vision)
                        Text(L.t("s_other_note")).font(.caption).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    } else {
                        Picker(L.t("s_answer_model"), selection: $model.answerModel) {
                            Text(L.t("s_answer_model_strong")).tag(SettingsModel.strongModel)
                            Text(L.t("s_answer_model_fast")).tag(SettingsModel.fastModel)
                            if ![SettingsModel.strongModel, SettingsModel.fastModel].contains(model.answerModel) {
                                Text(model.answerModel).tag(model.answerModel)
                            }
                        }
                    }
                    HStack {
                        Button(L.t("s_test")) { model.test() }.disabled(model.testing)
                        if model.testing { ProgressView().controlSize(.small) }
                        Text(model.testResult).font(.caption).foregroundStyle(.secondary).lineLimit(3)
                            .textSelection(.enabled)
                    }
                }
                Section(L.t("s_notes")) {
                    Toggle(L.t("s_open_viewer"), isOn: $model.openViewer)
                    Toggle(L.t("s_explain"), isOn: $model.explainEnabled)
                    Picker(L.t("s_detail"), selection: $model.detail) {
                        Text(L.t("s_detail_short")).tag("short")
                        Text(L.t("s_detail_medium")).tag("medium")
                        Text(L.t("s_detail_long")).tag("long")
                        if !["short", "medium", "long"].contains(model.detail) { Text(model.detail).tag(model.detail) }
                    }
                    Toggle(L.t("s_web_verify"), isOn: $model.webVerify)
                        .disabled(model.provider == "openai")          // that format has no web search of its own
                }
                Section(L.t("s_screen")) {
                    Toggle(L.t("s_screen_enabled"), isOn: $model.screenEnabled)
                    Picker(L.t("s_screen_target"), selection: $model.screenTarget) {
                        Text(L.t("s_screen_all")).tag("all")
                        Text(L.t("s_screen_main")).tag("main")
                        if model.screenTarget.hasPrefix("app:") || model.screenTarget == "display" {
                            Text(model.screenTarget).tag(model.screenTarget)
                        }
                    }
                    Toggle(L.t("s_send_images"), isOn: $model.sendImages)
                }
                Section(L.t("s_notify")) {
                    ForEach(SettingsModel.notifyKinds, id: \.self) { kind in
                        Toggle(L.t("n_" + kind), isOn: Binding(
                            get: { model.notifyTypes.contains(kind) },
                            set: { on in if on { model.notifyTypes.insert(kind) } else { model.notifyTypes.remove(kind) } }))
                    }
                }
                Section(L.t("s_retention")) {
                    ForEach(SettingsModel.retentionKeys, id: \.self) { key in
                        HStack {
                            Text(L.t("r_" + key))
                            Spacer()
                            TextField("", value: Binding(get: { model.retention[key] ?? 0 },
                                                         set: { model.retention[key] = max(0, $0) }), format: .number)
                                .frame(width: 60).multilineTextAlignment(.trailing)
                            Text(L.t("s_days"))
                        }
                    }
                    Text(L.t("s_retention_hint")).font(.caption).foregroundStyle(.secondary)
                }
            }
            .formStyle(.grouped)
            .disabled(model.busy)
            Divider()
            HStack {
                Text(model.status).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                Spacer()
                if model.busy { ProgressView().controlSize(.small) }
                Button(L.t("s_reload")) { model.load() }.disabled(model.busy)
                Button(L.t("s_save")) { model.save() }.keyboardShortcut(.defaultAction)
                    .disabled(model.busy || !model.isLoaded)
            }
            .padding(12)
        }
        .frame(minWidth: 480, minHeight: 560)
        .onAppear { model.load() }
    }
}
