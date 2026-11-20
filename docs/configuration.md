# Settings in detail

[繁體中文](configuration.zh-TW.md) · [简体中文](configuration.zh-CN.md) · [Back to the README](../README.md)

Most people only need the app's Settings window (menu bar icon → Settings…). This page is for everything else.

## The settings file

`~/.config/meeting-copilot/config.toml` overrides the defaults in
[`app/config.example.toml`](../app/config.example.toml), which documents every key. Common settings:

| Section | What |
|---|---|
| `[user]` | your name, the ways people address you (so questions to you are detected), and the language: `zh-TW` (Traditional Chinese), `zh-CN` (Simplified Chinese) or `en` (notes, answers, notifications and speech recognition follow it; empty = macOS language) |
| `[autopilot]` | auto-record on/off, start/stop delays, apps to ignore, `manual_mode`: what *Start recording* picks (`auto`, `room` for in-person meetings and phones on speaker, `call`) |
| `[copilot]` / `[explain]` | models, how often notes are analysed, answer length, web check |
| `[llm]` | who writes the notes and answers: your Claude subscription (default) or any OpenAI-compatible service |
| `[notify]` | which notifications to show, sounds, minimum gap |
| `[audio]` | pin a microphone so a Bluetooth headset connecting mid-call doesn't take over |
| `[screen]` | screen capture on/off, all displays, the main display or one app, images to the model |
| `[voice]` | voiceprint verification and its threshold |
| `[retention]` | days to keep audio, screenshots, notes, transcripts (0 = forever) |
| `[asr]` | speech-recognition models (`auto` picks them by language) and the mic level gate |
| `[paths]` | meetings folder, Minutes app, Claude Code |
| `[privacy]` | consent reminder, whether other participants' voiceprints are kept |

Restart the assistant after editing (the Settings window does this for you). After changing
`[user] language`, `[audio]`, `[privacy]` or `[paths]`, run the setup guide again (menu → Setup…): the language
picks the speech model, and the rest is mirrored into Minutes' settings.

## Another model

The notes and answers come from your Claude subscription by default. To use another service, open Settings, and
under "The model that writes notes and answers" choose "Another service": fill in its address, model name and
key, press Test, then Save. Or edit the settings file:

```toml
[llm]
provider = "openai"                       # any service that speaks the OpenAI chat format
base_url = "https://api.deepseek.com/v1"  # the service's address, up to /v1; a local model: "http://localhost:11434/v1"
model = "deepseek-chat"                   # the model's name there
fast_model = ""                           # optional: a faster, cheaper model that checks new lines for questions
vision = false                            # true when the model reads images: screen frames are then sent
```

Put the key in `~/.config/meeting-copilot/api_key` (one line; a model on your Mac needs none), or set
`MEETING_COPILOT_API_KEY`. Restart the assistant afterwards.

- Which model, and how well it does, is yours to choose; the prompts were tuned with Claude.
- The transcript and screen text of your meetings then go to that service.
- There is no online check of answers this way, and with `vision = false` speakers are not read from the screen
  (voices still name them).

## From the command line

The engine of the Mac app lives in `~/Library/Application Support/Meeting Copilot/engine` (a script install:
`~/meeting-copilot`). Call it `$ENGINE` below.

```bash
tail -F ~/meetings/live/current.md                                   # watch the live notes
tail -f "$ENGINE/app/state/autopilot.log"                            # what the assistant is doing
launchctl kickstart -k gui/$(id -u)/com.meeting-copilot.autopilot    # restart it
"$ENGINE/app/bin/copilot-known" Kubernetes TypeScript                # never explain these terms
"$ENGINE/app/bin/copilot-name" B Sam                                 # name speaker B (remembered next time)
```
