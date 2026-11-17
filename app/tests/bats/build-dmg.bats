#!/usr/bin/env bats
# app/macos/build.sh --dmg: the image of a release is built only in a clone of the public repository, so its build
# number and engine version name public commits. Each test runs the script in a scratch repository; a fake swift
# stops it at the first build step.
#
# No real home paths or e-mail addresses in this file.

load common

setup() {
  common_setup
  WORK="$BATS_TEST_TMPDIR/repo"
  mkdir -p "$WORK/app/macos"
  cp "$REPO/app/macos/build.sh" "$WORK/app/macos/build.sh"
  echo "first" > "$WORK/notes.txt"
  git -C "$WORK" init -q
  git -C "$WORK" add -A
  commit "first"
  printf '#!/bin/sh\necho "swift $*" >> "$FAKE_LOG"\nexit 3\n' > "$FAKE_BIN/swift"
  printf '#!/bin/sh\nexit 0\n' > "$FAKE_BIN/uv"
  chmod +x "$FAKE_BIN/swift" "$FAKE_BIN/uv"
  unset MC_DMG_ANY_ORIGIN
}

commit() { git -C "$WORK" -c user.name=Tester -c user.email=tester -c commit.gpgsign=false commit -q --allow-empty -m "$1"; }
origin() { git -C "$WORK" remote remove origin 2>/dev/null || true; git -C "$WORK" remote add origin "$1"; }
build() { run bash "$WORK/app/macos/build.sh" "$@"; }

@test "build.sh --dmg: a release tag outside the public repository is refused before anything is built" {
  git -C "$WORK" tag v1.2.3
  origin "https://example.com/someone/fork.git"
  build --dmg
  [ "$status" -eq 1 ]
  [[ "$output" == *"v1.2.3 is a release: build its image in a clone of https://github.com/claude-meeting/claude-meeting"* ]]
  [[ "$output" == *"origin here: https://example.com/someone/fork.git"* ]]
  not_logged "swift build"
  git -C "$WORK" remote remove origin
  build --dmg
  [ "$status" -eq 1 ]
  [[ "$output" == *"origin here: none"* ]]
  origin "https://github.com/claude-meeting/claude-meeting-notes.git"     # a name that only starts the same
  build --dmg
  [ "$status" -eq 1 ]
}

@test "build.sh --dmg: a release without its own signing identity is refused before anything is built" {
  git -C "$WORK" tag v1.2.3
  origin "https://github.com/claude-meeting/claude-meeting.git"
  unset MEETING_COPILOT_SIGNING_NAME
  MEETING_COPILOT_SIGNING_DIR="$BATS_TEST_TMPDIR/signing" build --dmg
  [ "$status" -eq 1 ]
  [[ "$output" == *"sign it with its own identity"* ]]
  MEETING_COPILOT_SIGNING_NAME="meeting-copilot local signing" MEETING_COPILOT_SIGNING_DIR="$BATS_TEST_TMPDIR/signing" build --dmg
  [ "$status" -eq 1 ]                                                     # the everyday identity
  MEETING_COPILOT_SIGNING_NAME="release" MEETING_COPILOT_SIGNING_DIR="" build --dmg
  [ "$status" -eq 1 ]
  not_logged "swift build"
}

@test "build.sh --dmg: a release refuses untracked files in the engine folders" {
  git -C "$WORK" tag v1.2.3
  origin "https://github.com/claude-meeting/claude-meeting.git"
  export MEETING_COPILOT_SIGNING_NAME="release" MEETING_COPILOT_SIGNING_DIR="$BATS_TEST_TMPDIR/signing"
  mkdir -p "$WORK/app/lib" "$WORK/app/bin"
  echo "x" > "$WORK/app/bin/mic-users"                     # a helper install.sh builds: allowed
  echo "x" > "$WORK/app/bin/tool.py"
  printf 'app/bin/mic-users\n' > "$WORK/.gitignore"
  git -C "$WORK" add .gitignore app/bin/tool.py && commit "ignore" && git -C "$WORK" tag -f v1.2.3 >/dev/null
  build --dmg
  [ "$status" -eq 3 ]                                                     # reaches the fake swift
  echo "left over" > "$WORK/app/lib/notes.txt"
  build --dmg
  [ "$status" -eq 1 ]
  [[ "$output" == *"not part of it"* ]]
  [[ "$output" == *"app/lib/notes.txt"* ]]
}

@test "build.sh --dmg: a release tag in a clone of the public repository goes on to build" {
  git -C "$WORK" tag v1.2.3
  origin "https://github.com/claude-meeting/claude-meeting.git"
  export MEETING_COPILOT_SIGNING_NAME="release" MEETING_COPILOT_SIGNING_DIR="$BATS_TEST_TMPDIR/signing"
  build --dmg
  [ "$status" -eq 3 ]                                                     # the fake swift
  logged "swift build -c release"
  origin "git@""github.com:claude-meeting/claude-meeting.git"             # split so it does not read as an e-mail address
  build --dmg
  [ "$status" -eq 3 ]
}

@test "build.sh --dmg: an image to test with builds in any repository" {
  origin "https://example.com/someone/fork.git"
  build --dmg                                                             # no tag at all
  [ "$status" -eq 3 ]
  git -C "$WORK" tag v1.2.3
  commit "second"
  build --dmg                                                             # after the tag: v1.2.3-1-g<commit>
  [ "$status" -eq 3 ]
  git -C "$WORK" tag v1.2.4
  echo "changed" >> "$WORK/notes.txt"
  build --dmg                                                             # at the tag, changed: v1.2.4-dirty
  [ "$status" -eq 3 ]
  git -C "$WORK" checkout -q notes.txt
  MC_DMG_ANY_ORIGIN=1 build --dmg
  [ "$status" -eq 3 ]
  build                                                                   # the .app alone
  [ "$status" -eq 3 ]
  [ "$(logged_count "swift build -c release")" -eq 5 ]
}
