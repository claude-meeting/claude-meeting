# Development

[Back to the README](../README.md)

## Install from a git checkout (no Mac app)

Needs Xcode Command Line Tools (`xcode-select --install`) and [uv](https://docs.astral.sh/uv/) (`brew install uv`).

```bash
git clone https://github.com/claude-meeting/claude-meeting.git ~/meeting-copilot
```

```bash
~/meeting-copilot/install.sh
```

The installer can be re-run any time; it skips what is already done (`--get-minutes` and `--get-claude` install
those two as well). It then lists the manual steps: the permissions, and enrolling your voiceprint after your first
meeting:

```bash
~/meeting-copilot/app/asr-venv/bin/python ~/meeting-copilot/app/tools/enroll_voiceprint.py ~/meetings/<meeting-name>
```

Pass the meeting's path without the file extension, or a `.wav` of 30–60 s of you talking. If the installer says
Claude Code is not signed in, run `claude auth login` first. To remove it: `~/meeting-copilot/uninstall.sh`
(`--purge` also moves the models and your settings to the Trash).

## Build the Mac app

Run `install.sh` once (it builds and signs the helpers), then `app/macos/build.sh --dmg` (needs Xcode and uv; the
first `--dmg` also needs the network, to install dmgbuild from `app/macos/dmg-requirements.txt`, hashes checked).
A release is built from a clean clone of this repository at the tagged commit (`vX.Y.Z`), after `make test-ci`
passes there.
At a version tag, `build.sh --dmg` runs only in a clone of this repository; in a fork, `MC_DMG_ANY_ORIGIN=1` makes
an image to test with.
`scripts/demo-meeting.py` plays a made-up meeting into the app, for screenshots: quit claude-meeting first, since a
second copy of the same version hands over to the running one.

## How it works

Speech recognition: Chinese uses TEA-ASR-1.1, tuned for Taiwanese Mandarin with English terms and picked by
benchmark (see `reports/`, in Traditional Chinese); Simplified Chinese uses Qwen3-ASR, which writes Simplified
characters itself (not benchmarked on mainland speech yet); English uses Qwen3-ASR with Parakeet as the fallback
(the two tied for best in an English benchmark). Speakers are named from people heard in earlier meetings, from
the tile the meeting app marks as speaking (your own tile never names anyone else), or by you
(`copilot-name B Sam`). Echo of the call in your microphone is filtered out, and so is speech from people near you, using
your voiceprint.

```
Minutes (launchd job as Minutes.app) ── mic stem ──┐
            └─────────────────────── call stem ────┤
                                                    ▼
                               live_transcriber.py: VAD → echo filter → voiceprint → TEA-ASR (MLX)
                                                    │  one JSON line per utterance, ~1-2 s
screen-capture (ScreenCaptureKit, 1 fps, changed     ▼
frames only) → screen-ocr (Apple Vision) ──→ autopilot.py ──→ live notes + notifications
                                                    │
                                     claude -p (your account): notes every 15 s,
                                     instant answers on questions, web check
```

| Path | Purpose |
|---|---|
| `app/bin/autopilot.py` | main loop: detects calls, starts/stops recording, feeds notes, runs Claude |
| `app/bin/live_transcriber.py` | per-speaker live transcription of the growing recording |
| `app/bin/minutes` | wrapper that starts recording through launchd so macOS attributes it to Minutes.app |
| `app/bin/retention.py` | daily data lifecycle |
| `app/lib/` | speech recognition, voiceprint, screen diffing, instant answers, config |
| `app/src/` | Swift helpers: microphone users, screen capture, screen OCR, frames to video |
| `app/tools/` | offline transcription, voiceprint enrollment, validation sets, Minutes config sync, settings editing, code signing |
| `app/macos/` | the Mac app (SwiftUI) and its packaging |
| `reports/` | speech-recognition benchmark report (Traditional Chinese) |

## Tests

```bash
make test
```

runs everything that needs no models and no Claude account: the Python unit, integration and end-to-end
tests (the real engine against a fake Minutes, a fake Claude Code and fake system tools in
`app/tests/fakes/`, with a coverage threshold), the Swift tests (`swift test` in `app/macos`), the
script tests (`bats app/tests/bats`) and `shellcheck`. `make test-pr` runs the same on the committed tree and
writes a report with the full log to `app/.ci-reports/`, with your home folder written as `~` and the temporary
folder as `$TMPDIR`; `make test-ci` does that in a macOS 15 virtual machine set up from nothing. The report stays
on your Mac unless you add `MC_CI_POST=1`: then the commit must be pushed, the report goes to a `ci-evidence`
branch, the commit gets a status and its pull request a comment, all as the account `gh` is signed in to.
`make test-live` adds the checks that need the real models and your Claude account, on a recording of your own
with two speakers: `MC_LIVE_MEETING=~/meetings/<name> make test-live` (the recording's path without its
extension; `MC_LIVE_START=mm:ss` picks a point where both talk). It runs a replay of that recording
(`app/tests/simulate_live.py`), the transcriber resume test and a fresh-HOME install from the built app.
`scripts/test.sh --only unit|integration|e2e|swift|bats` runs one group; `make coverage` opens the HTML report.
