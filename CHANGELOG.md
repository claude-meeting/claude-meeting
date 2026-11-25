# Changelog

The project's public history starts here, with its first public release.

## v0.1.21 (2026-11-25)

- **A call whose audio is not recorded is caught and handled**: a call's audio that never reached the recording (not
  only one that stopped) is noticed while a call goes on, and the recording is restarted up to 3 times, each after a
  longer wait (`[autopilot] call_audio_restarts`); your own talking no longer hides it. With no restart left, the
  recording turns to room mode for now, so the others the microphone hears are kept, and back to call mode when the
  call's audio returns. After a meeting, what the call said that the live transcript missed is taken from the saved
  call track and added to the meeting record (`[autopilot] fill_call_gaps`; `tools/fill_call_gaps.py` for an older
  meeting). When the next call has already begun, the record is written at once and filled after that call.
- **Muting in a web call no longer ends its recording**, and a recording started from the menu while muted in a web
  call is a call, not a room: Chrome's sign that a web call is on now counts as a call.
- **The live-notes window shows the recording**: recording or not and for how long, the mode, a switch to the other
  mode, and start or stop. The menu shows the mode at normal size.
- **An app left running after an update says so**: when a newer app was installed over the running one, the menu and
  the notes window offer to reopen it.
- **A recording with almost nothing in it is not waited on**: Minutes ends such a job as "needs review" (a few seconds
  started and stopped by mistake); the engine now takes that as done.
- **Lighter on the Mac during meetings**: only the newest changed frame of the screen is read for text (it was every
  changed frame, the most CPU of anything in a video call), and none while the recording reports audio input
  overload.

## v0.1.20 (2026-11-20)

- **Room mode: in-person meetings and phones on speaker** (#6). The microphone is the room: every voice on it is kept
  and told apart (Others A, B, C), and your voiceprint marks your lines, where call mode takes the microphone as you.
  By default a recording you start (from the menu, or in Minutes) with no other app on the microphone uses room mode
  once your voiceprint is recorded (and `[voice] verify` is on); otherwise it is a call, as before. Auto-started
  recordings are calls. The menu shows the mode and switches it; a switch applies to what is said after it. A room the
  engine chose turns into a call when another app (a call app, a browser tab) takes the microphone; a room you chose
  (in the menu, or as the Settings default *Recording started from the menu*, `[autopilot] manual_mode`) stays one and
  keeps recording when a call ends: stop it yourself. In a room, speakers are not named from the video tiles, and a
  room chosen without a voiceprint counts every voice, yours too, as someone else.
  Telling people apart from one microphone is harder than on a call: one person is often shown as two speakers (give
  both the same name), and some lines can be given to the wrong side, others' as yours or yours as others' (on a
  public meeting-room test set, about 1 in 7 lines marked as yours and 1 in 12 of yours).

## v0.1.19 (2026-11-18)

- **Simplified Chinese documentation**: `README.zh-CN.md` and `docs/configuration.zh-CN.md`, with screenshots and a
  demo taken in Simplified Chinese macOS.
- **The app's Simplified Chinese reads as mainland Chinese**: 实时笔记 (not 即时笔记), 会应用到, 按声音; the engine's
  Simplified prompts lose Taiwan-style wording too.
- **macOS 15's name for Login Items** in the setup guide, the READMEs and the uninstaller: Login Items & Extensions
  (登入項目與延伸功能, 登录项与扩展).

## v0.1.18 (2026-11-18)

Fixes from the first reports (claude-meeting/claude-meeting #1–#5):

- **The transcript after each meeting no longer fails when Minutes uses another model** (#1). Recordings ask Minutes
  for its `small` Whisper model, which setup only fetched when it was Minutes' own choice; every post-meeting job
  then failed and the meeting got no file in `~/meetings`. Setup and the engine now fetch it (never during a
  meeting), a recording falls back to the model you have until then, and a failed post-meeting job shows up in the
  menu and as a notification.
- **"No permission to capture the screen" goes away once it is granted** (#2): it is judged per recording.
- **The screen helper's name** (#3): System Settings may list it as *MeetingCopilotScreen*; the guide, README and
  messages now say so, and that it is added switched off.
- **The installer no longer fails while the service is running** (#4): it waits for the old job to unload and
  counts a running job as started when launchd reports an error.
- **A meeting that ends without a record is reported**: half an hour after a meeting of a minute or more, if
  `~/meetings` still has no file for it, a notification and the menu say so, whatever the cause.
- **Updating from Meeting Copilot** (#5): the app moves the old app to the Trash (and says so), and the menu warns while
  the engine it carries is not installed yet. The README has an *Updating* section.

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
