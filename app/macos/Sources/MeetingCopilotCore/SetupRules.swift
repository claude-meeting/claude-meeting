import Foundation

/// The setup guide's rules: when a step counts as done and when its button may be pressed. Kept out of the
/// SwiftUI view so the order of the steps can be tested (on a fresh Mac, Install could be pressed before
/// Minutes existed).
public enum SetupRules {
    /// Speech recognition is done when the engine is installed, matches this app's version, and has the
    /// speech model of the current language (`asr_missing` from status.json is empty).
    public static func engineDone(installed: Bool, outdated: Bool, asrMissing: String) -> Bool {
        installed && !outdated && asrMissing.isEmpty
    }

    /// Why Install / Update cannot be pressed now (an `L` key for the tooltip, "" when only the name is
    /// missing), or nil when it can. A meeting in progress comes first: it also blocks an update.
    public static func installBlocked(name: String, minutesReady: Bool, engineBusy: Bool) -> String? {
        if engineBusy { return "setup_busy" }
        if !minutesReady { return "setup_needs_minutes" }
        if name.trimmingCharacters(in: .whitespaces).isEmpty { return "" }
        return nil
    }

    /// The voiceprint is recorded with the engine's tools, so it waits for speech recognition.
    public static func voiceEnabled(engineDone: Bool, recording: Bool) -> Bool {
        engineDone && !recording
    }

    /// The model that writes the notes and answers is ready: the user's Claude account is signed in, or, with
    /// another service chosen ([llm] provider = "openai"), it has an address and a model name. Whether that
    /// service answers is the settings window's "Test" button, not a step here.
    public static func modelReady(provider: String?, claudeSignedIn: Bool, baseURL: String?, model: String?) -> Bool {
        guard provider == "openai" else { return claudeSignedIn }
        let filled = { (s: String?) in !(s ?? "").trimmingCharacters(in: .whitespaces).isEmpty }
        return filled(baseURL) && filled(model)
    }

    /// Everything needed for the first meeting.
    public static func allDone(minutes: Bool, model: Bool, engine: Bool) -> Bool {
        minutes && model && engine
    }
}
