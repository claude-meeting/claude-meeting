import SwiftUI
import MeetingCopilotCore

/// The current meeting's speakers (A, B, C...) and their names, through bin/copilot-name, so naming
/// here does exactly what the command does: the notes use the name, the voiceprint is remembered.
@MainActor
final class SpeakersModel: ObservableObject {
    struct Speaker: Identifiable, Equatable {
        var id: String { who }
        let who: String
        var name: String
        let lines: Int
    }

    @Published var meeting = ""
    @Published var speakers: [Speaker] = []
    @Published var edits: [String: String] = [:]
    @Published var message = ""
    let engine: Engine

    init(engine: Engine) { self.engine = engine }

    private var tool: String { engine.appDir.appendingPathComponent("bin/copilot-name").path }

    func load(keepMessage: Bool = false) {
        Task {
            let r = await Shell.runStatus(tool, ["--json"])
            guard let data = r.out.data(using: .utf8),
                  let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                speakers = []
                edits = [:]
                message = (r.out + "\n" + r.err).trimmingCharacters(in: .whitespacesAndNewlines); return
            }
            meeting = root["meeting"] as? String ?? ""
            speakers = (root["speakers"] as? [[String: Any]] ?? []).map {
                Speaker(who: $0["who"] as? String ?? "", name: $0["name"] as? String ?? "", lines: $0["lines"] as? Int ?? 0)
            }
            edits = Dictionary(speakers.map { ($0.who, $0.name) }, uniquingKeysWith: { a, _ in a })
            if !keepMessage { message = "" }
        }
    }

    func save() {
        let changed = speakers.filter { (edits[$0.who] ?? "").trimmingCharacters(in: .whitespaces) != $0.name
            && !(edits[$0.who] ?? "").trimmingCharacters(in: .whitespaces).isEmpty }
        Task {
            var results: [String] = []
            for s in changed {
                let r = await Shell.runStatus(tool, [s.who, (edits[s.who] ?? "").trimmingCharacters(in: .whitespaces)])
                results.append((r.out + "\n" + r.err).trimmingCharacters(in: .whitespacesAndNewlines))
            }
            message = results.isEmpty ? L.t("settings_no_change") : results.joined(separator: "\n")
            load(keepMessage: true)                 // the saved-speaker confirmation stays visible
        }
    }
}

/// People remembered by voice across meetings: ~/.config/meeting-copilot/people.json.
@MainActor
final class PeopleModel: ObservableObject {
    struct Person: Identifiable, Equatable {
        var id: String { name }
        let name: String
        let lines: Int
    }

    @Published var people: [Person] = []
    @Published var renaming: String?
    @Published var newName = ""
    @Published var message = ""
    private var file: URL {
        URL(fileURLWithPath: UserConfig.path("voice", "people_file", default: "~/.config/meeting-copilot/people.json"))
    }

    private func read() -> [String: Any] {
        guard let data = try? Data(contentsOf: file),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return obj
    }

    private func write(_ obj: [String: Any]) {
        if let data = try? JSONSerialization.data(withJSONObject: obj) { try? data.write(to: file, options: .atomic) }
        load()
    }

    func load() {
        people = read().map { Person(name: $0.key, lines: ($0.value as? [String: Any])?["count"] as? Int ?? 0) }
            .sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }

    func rename(_ old: String, to new: String) {
        let new = new.trimmingCharacters(in: .whitespaces)
        var obj = read()
        guard !new.isEmpty, new != old else { return }
        if obj[new] != nil {                 // never silently replace another person's voiceprint
            message = L.t("people_name_taken")
            return
        }
        guard let entry = obj.removeValue(forKey: old) else { return }
        obj[new] = entry
        message = ""
        write(obj)
    }

    func delete(_ name: String) {
        var obj = read()
        obj.removeValue(forKey: name)
        write(obj)
    }
}

struct SpeakersView: View {
    @ObservedObject var model: SpeakersModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(L.t("speakers_title")).font(.headline)
            Text(L.t("speakers_hint")).font(.callout).foregroundStyle(.secondary)
            if model.speakers.isEmpty {
                Text(L.t("speakers_none")).foregroundStyle(.secondary).padding(.vertical, 20)
            } else {
                Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 8) {
                    ForEach(model.speakers) { s in
                        GridRow {
                            Text("\(L.t("others")) \(s.who)").font(.callout.bold())
                            TextField(L.t("speakers_name"), text: Binding(get: { model.edits[s.who] ?? "" },
                                                                         set: { model.edits[s.who] = $0 }))
                                .frame(width: 180)
                            Text(String(format: L.t("speakers_lines"), s.lines)).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            if !model.message.isEmpty { Text(model.message).font(.caption).foregroundStyle(.secondary) }
            HStack {
                Button(L.t("s_reload")) { model.load() }
                Spacer()
                Button(L.t("s_save")) { model.save() }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(18)
        .frame(minWidth: 420, minHeight: 260)
        .onAppear { model.load() }
    }
}

struct PeopleView: View {
    @ObservedObject var model: PeopleModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(L.t("people_title")).font(.headline)
            Text(L.t("people_hint")).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if model.people.isEmpty {
                Text(L.t("people_none")).foregroundStyle(.secondary).padding(.vertical, 20)
            }
            if !model.message.isEmpty { Text(model.message).font(.caption).foregroundStyle(.orange) }
            List(model.people) { p in
                HStack {
                    if model.renaming == p.name {
                        TextField("", text: $model.newName).frame(width: 180)
                            .onSubmit { model.rename(p.name, to: model.newName); model.renaming = nil }
                        Button(L.t("s_save")) { model.rename(p.name, to: model.newName); model.renaming = nil }
                    } else {
                        Text(p.name)
                        Text(String(format: L.t("speakers_lines"), p.lines)).font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        Button(L.t("people_rename")) { model.newName = p.name; model.renaming = p.name }
                        Button(L.t("people_delete"), role: .destructive) { model.delete(p.name) }
                    }
                }
            }
        }
        .padding(18)
        .frame(minWidth: 460, minHeight: 360)
        .onAppear { model.load() }
    }
}
