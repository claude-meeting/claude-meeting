import SwiftUI
import MeetingCopilotCore

/// Follows a growing notes file and parses it.
@MainActor
final class NotesModel: ObservableObject {
    @Published private(set) var blocks: [NoteEntry] = []
    @Published private(set) var file: URL?
    private var size: UInt64 = 0
    private var timer: Timer?

    func follow(_ url: URL?) {
        let resolved = url?.resolvingSymlinksInPath()
        if resolved != file {
            file = resolved
            size = 0
            blocks = []
        }
        reload()
        if timer == nil {
            let t = Timer(timeInterval: 0.5, repeats: true) { [weak self] _ in
                Task { @MainActor in self?.reload() }
            }
            RunLoop.main.add(t, forMode: .common)          // keeps ticking while a window is dragged
            timer = t
        }
    }

    /// The window closed: stop reading the file until it is shown again.
    func stop() {
        timer?.invalidate()
        timer = nil
    }

    private func reload() {
        guard let file,
              let attrs = try? FileManager.default.attributesOfItem(atPath: file.path),
              let s = attrs[.size] as? UInt64, s != size,
              let text = try? String(contentsOf: file, encoding: .utf8) else { return }
        size = s
        blocks = NotesParser.parse(text)
    }
}

struct NotesView: View {
    @ObservedObject var model: NotesModel
    var openSpeakers: () -> Void = {}
    /// Earlier meetings' notes, and opening one (nil: back to the meeting in progress).
    var recent: () -> [RecentNote] = { [] }
    var open: (URL?) -> Void = { _ in }
    @AppStorage("highlightsOnly") private var highlightsOnly = false
    @AppStorage("historyShown") private var historyShown = false
    @State private var follow = true
    @State private var meetings: [RecentNote] = []

    /// Earlier meetings, in a side list that folds away: the window otherwise shows only the latest meeting, and
    /// the menu-bar list was not where one looks for them.
    private var history: some View {
        let shown = model.file?.resolvingSymlinksInPath()
        return ScrollView {
            VStack(alignment: .leading, spacing: 2) {
                Text(L.t("recent_meetings")).font(.caption.bold()).foregroundStyle(.secondary)
                    .padding(.horizontal, 8).padding(.bottom, 4)
                row(L.t("current_meeting"), systemImage: "dot.radiowaves.left.and.right", selected: false) { open(nil) }
                Divider().padding(.vertical, 4)
                if meetings.isEmpty {
                    Text(L.t("no_recent_meetings")).font(.callout).foregroundStyle(.secondary).padding(.horizontal, 8)
                }
                ForEach(meetings) { note in
                    row(note.title, systemImage: nil, selected: note.url.resolvingSymlinksInPath() == shown) {
                        open(note.url)
                    }
                }
            }
            .padding(8)
        }
        .frame(width: 220)
        .background(.background.secondary)
    }

    private func row(_ title: String, systemImage: String?, selected: Bool,
                     action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if let systemImage { Image(systemName: systemImage) }
                Text(title).lineLimit(1).truncationMode(.tail)
                Spacer(minLength: 0)
            }
            .font(.callout)
            .padding(.horizontal, 8).padding(.vertical, 5)
            .background(selected ? Color.accentColor.opacity(0.25) : .clear, in: RoundedRectangle(cornerRadius: 6))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    var body: some View {
        HStack(spacing: 0) {
            if historyShown {
                history
                Divider()
            }
            VStack(spacing: 0) {
                HStack {
                    Button {
                        withAnimation(.easeOut(duration: 0.15)) { historyShown.toggle() }
                    } label: {
                        Image(systemName: "sidebar.left")
                    }
                    .buttonStyle(.borderless)
                    .help(L.t("meeting_history"))
                    Text(model.file.map { RecentNotes.title(for: $0) } ?? L.t("notes_title"))
                        .font(.headline).lineLimit(1)
                    Spacer()
                    Button(L.t("speakers_button"), systemImage: "person.2") { openSpeakers() }.controlSize(.small)
                    Toggle(L.t("highlights_only"), isOn: $highlightsOnly).toggleStyle(.switch).controlSize(.small)
                    Toggle(L.t("follow"), isOn: $follow).toggleStyle(.switch).controlSize(.small)
                }
                .padding(.horizontal, 14).padding(.vertical, 8)
                Divider()
                if model.blocks.isEmpty {
                    Spacer()
                    Text(L.t("no_notes")).foregroundStyle(.secondary)
                    Spacer()
                } else {
                    ScrollViewReader { proxy in
                        ScrollView {
                            LazyVStack(alignment: .leading, spacing: 6) {
                                ForEach(visible) { entry in
                                    BlockView(block: entry.block).id(entry.id)
                                }
                                Color.clear.frame(height: 1).id("end")
                            }
                            .padding(14)
                            .textSelection(.enabled)
                        }
                        .onChange(of: model.blocks) {
                            if follow { withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo("end", anchor: .bottom) } }
                        }
                        .onAppear { proxy.scrollTo("end", anchor: .bottom) }
                    }
                }
            }
        }
        .frame(minWidth: 420, minHeight: 360)
        .onAppear { meetings = recent() }
        .onChange(of: model.file) { meetings = recent() }
        .onChange(of: historyShown) { meetings = recent() }
    }

    private var visible: [NoteEntry] {
        highlightsOnly ? model.blocks.filter { $0.block.isHighlight } : model.blocks
    }
}

struct BlockView: View {
    let block: NoteBlock

    var body: some View {
        switch block {
        case .title(let s):
            Text(s).font(.title2.bold()).padding(.bottom, 2)
        case .started(let s):
            Text(s).font(.caption).foregroundStyle(.secondary)
        case .time(let s):
            HStack(spacing: 6) {
                Rectangle().fill(.quaternary).frame(height: 1)
                Text(s).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                Rectangle().fill(.quaternary).frame(height: 1)
            }
            .padding(.vertical, 4)
        case .transcript(let time, let speaker, let text):
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(time).font(.caption.monospacedDigit()).foregroundStyle(.tertiary)
                Text(speaker).font(.callout.bold()).foregroundStyle(speakerColor(speaker))
                    .frame(minWidth: 52, alignment: .leading)
                Text(text).font(.callout)
            }
        case .answer(let heading, let body):
            VStack(alignment: .leading, spacing: 4) {
                Text(heading).font(.callout.bold())
                if !body.isEmpty { Text(markdown(body)).font(.callout) }
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(heading.hasPrefix("❓") ? Color.orange.opacity(0.14) : Color.accentColor.opacity(0.10),
                        in: RoundedRectangle(cornerRadius: 8))
        case .item(let s):
            Text(markdown(s)).font(.callout)
                .padding(.horizontal, 10).padding(.vertical, 6)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(NotesParser.isMine(s) ? Color.yellow.opacity(0.18) : Color.secondary.opacity(0.08),
                            in: RoundedRectangle(cornerRadius: 6))
        case .summary(let s):
            Text(markdown("**\(L.t("summary"))**　\(s)")).font(.callout).foregroundStyle(.secondary)
        case .info(let s):
            Text(s).font(.caption).foregroundStyle(.secondary)
        case .text(let s):
            Text(markdown(s)).font(.callout)
        }
    }

    private func markdown(_ s: String) -> AttributedString {
        (try? AttributedString(markdown: s, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
            ?? AttributedString(s)
    }

    private func speakerColor(_ speaker: String) -> Color {
        if speaker == "我" || speaker == "Me" { return .accentColor }
        let palette: [Color] = [.teal, .purple, .pink, .brown, .indigo, .mint, .orange]
        // Stable across launches (hashValue is seeded per process): the same person keeps a colour.
        return palette[speaker.unicodeScalars.reduce(0) { ($0 &* 31 &+ Int($1.value)) & 0xFFFF } % palette.count]
    }
}

/// `MeetingCopilot --snapshot <notes.md> <out.png> [zh|en]`: render a notes file to an image, for
/// checking the layout without a screen (tests, documentation).
@MainActor
enum NotesSnapshot {
    static func run(_ args: [String]) -> Bool {
        guard let i = args.firstIndex(of: "--snapshot"), args.count > i + 2 else { return false }
        if args.count > i + 3 { L.language = args[i + 3] }
        let text = (try? String(contentsOfFile: args[i + 1], encoding: .utf8)) ?? ""
        let blocks = NotesParser.parse(text)
        let view = VStack(alignment: .leading, spacing: 6) {
            ForEach(blocks) { BlockView(block: $0.block) }
        }
        .padding(14).frame(width: 560, alignment: .leading).background(Color.white)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        guard let image = renderer.nsImage, let tiff = image.tiffRepresentation,
              let png = NSBitmapImageRep(data: tiff)?.representation(using: .png, properties: [:]) else { return true }
        try? png.write(to: URL(fileURLWithPath: args[i + 2]))
        print("\(blocks.count) blocks -> \(args[i + 2])")
        return true
    }
}
