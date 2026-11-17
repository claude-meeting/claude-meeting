# Changelog

The project's public history starts here, with its first public release.

## v0.1.17 (2026-11-16)

The first public release. What you get:

- **Recording that starts by itself.** When an app starts using the microphone for a call (Zoom, Meet in a
  browser, Teams, LINE, Slack and others), [Minutes](https://github.com/silverstein/minutes) records it, and the
  recording stops when the call ends. You can also start, stop or pause it from the menu bar.
- **A live transcript per speaker.** Each line shows a second or two after it is said, marked as yours or someone
  else's. Thirty seconds of your voice, recorded once, keep people near you from being taken for you. Other voices
  are told apart, then named from the tile the meeting app lights for whoever is talking, or from people you named
  before.
- **Live notes.** Every 15 seconds the model picks out to-dos, decisions, deadlines, risks and questions put to
  you, and notifies you of the ones that concern you. Lines that are only filler words stay out of the notes; the
  transcript keeps them.
- **Instant answers.** A question put to you, or a term you may not know, gets a short answer within a few
  seconds. With Claude, a web search checks it in the background.
- **Screen reading.** Shared slides and documents become text the model can use (this part needs macOS 26).
- **After the call.** The full transcript with speaker names, a summary, decisions and action items, all in
  `~/meetings`. Earlier meetings open from the menu or from the side panel of the notes window.
- **Speech recognition on your Mac.** It runs on Apple Silicon with MLX; no audio leaves the Mac for it.
  Traditional Chinese uses TEA-ASR, tuned for Taiwanese Mandarin mixed with English terms. Simplified Chinese and
  English use Qwen3-ASR. When a line comes out garbled, a smaller second model tries it again. A list of your own
  fixes (`heard => meant`) corrects words it keeps getting wrong.
- **The model of your choice for notes and answers.** By default, your own Claude account through Claude Code. Or
  any service that speaks the OpenAI chat format, such as DeepSeek, Qwen, OpenAI or a model running on your Mac.
  Settings has a Test button to check it.
- **Three languages.** Traditional Chinese, Simplified Chinese (mainland usage) and English, for the app, the
  notes, the answers, the notifications and speech recognition. It follows your Mac's language, or the one you
  pick in Settings.
- **A Mac app.** It sits in the menu bar and the Dock, with a live notes window, Settings and the list of people it
  remembers. Its setup guide installs Minutes, Claude Code and the speech models (about 7 GB), opens the right
  permission pages and records your voiceprint.
- **Install from a disk image.** Drag claude-meeting to Applications. The app is self-signed, not notarized by
  Apple, so macOS blocks it the first time: click Open Anyway in System Settings → Privacy & Security. The README
  shows each step. You need an Apple Silicon Mac with macOS 15 or later; 16 GB of memory is recommended.
- **Privacy.** Audio, screenshots, transcripts and notes stay on your Mac. Only the model you chose gets transcript
  lines, screen text and, if you allow it, screen images, under your own account or key. claude-meeting has no
  server and sends nothing to its maker: no analytics, no crash reports. Voiceprints of people you name are kept
  until you remove them, and `[privacy] store_voiceprints = false` keeps only yours. Old files go to the Trash
  after the number of days set in Settings; nothing is deleted outright. A reminder to tell people they are being
  recorded can be switched on.
- **Uninstall** with one command. It moves the models and settings to the Trash and leaves your meetings alone.
