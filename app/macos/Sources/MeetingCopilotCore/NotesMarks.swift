// Written by app/tools/hans_strings.py from the engine's texts (app/lib/i18n.py STRINGS): run it after
// changing them. The words of the live notes the viewer looks for, in every language the engine writes.
public enum NotesMarks {
    /// How the line under a meeting's title starts (notes_title).
    public static let started = ["開始：", "开始：", "Started: "]
    /// Labels of the items that concern the user (mentioned, my to-do, asked me): highlighted.
    public static let mine = ["🙋 點名", "✅ 我的待辦", "❓ 問我", "🙋 点名", "✅ 我的待办", "❓ 问我", "🙋 Mentioned", "✅ My to-do", "❓ Asked me"]
}
