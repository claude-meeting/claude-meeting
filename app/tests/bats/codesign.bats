#!/usr/bin/env bats
# app/tools/codesign.sh with fake security/codesign: the ad-hoc fallbacks and the guard that
# never rewrites an unreadable keychain search list.

load common

setup() {
  common_setup
  SIGNING="$HOME/.config/meeting-copilot/signing"
  mkdir -p "$SIGNING"
  : > "$SIGNING/signing.keychain-db"                 # an existing identity: create_identity is skipped
  echo secret > "$SIGNING/keychain-password"
  TOOL="$BATS_TEST_TMPDIR/helper-binary"
  : > "$TOOL"
  export FAKE_IDENTITY='  1) 0123456789ABCDEF "meeting-copilot local signing"'
}

@test "codesign: no arguments prints the usage and exits 2" {
  run bash "$REPO/app/tools/codesign.sh"
  [ "$status" -eq 2 ]
  [[ "$output" == *"Usage: codesign.sh <path>"* ]]
  not_logged "security"
  not_logged "codesign"
}

@test "codesign: empty keychain search list -> ad-hoc signing, list never rewritten" {
  run bash "$REPO/app/tools/codesign.sh" "$TOOL"
  [ "$status" -eq 0 ]
  [[ "$output" == *"keychain search list unreadable"* ]]
  [[ "$output" == *"signed ad-hoc"* ]]
  logged "security unlock-keychain -p secret $SIGNING/signing.keychain-db"
  logged "security find-identity -p codesigning $SIGNING/signing.keychain-db"
  logged "security list-keychains -d user"
  not_logged "list-keychains -d user -s"
  logged "codesign -s - -f $TOOL"
  not_logged "codesign -f -s 0123456789ABCDEF"
  [ ! -d "$SIGNING/.lock" ]                          # the lock is released
}

@test "codesign: several paths are all signed ad-hoc in the fallback" {
  OTHER="$BATS_TEST_TMPDIR/other-binary"; : > "$OTHER"
  run bash "$REPO/app/tools/codesign.sh" "$TOOL" "$OTHER"
  [ "$status" -eq 0 ]
  logged "codesign -s - -f $TOOL"
  logged "codesign -s - -f $OTHER"
  not_logged "list-keychains -d user -s"
}

@test "codesign: identity missing from the keychain -> ad-hoc signing without touching the list" {
  unset FAKE_IDENTITY
  run bash "$REPO/app/tools/codesign.sh" "$TOOL"
  [ "$status" -eq 0 ]
  [[ "$output" == *"signing certificate not found"* ]]
  [[ "$output" == *"signed ad-hoc"* ]]
  logged "codesign -s - -f $TOOL"
  not_logged "list-keychains"
  [ ! -d "$SIGNING/.lock" ]
}

@test "codesign: readable list -> our keychain is added for signing and the list restored" {
  export FAKE_KEYCHAINS="$BATS_TEST_TMPDIR/keychains.txt"
  LOGIN="$HOME/Library/Keychains/login.keychain-db"
  printf '    "%s"\n' "$LOGIN" > "$FAKE_KEYCHAINS"
  run bash "$REPO/app/tools/codesign.sh" "$TOOL"
  [ "$status" -eq 0 ]
  [[ "$output" == *'signed with "meeting-copilot local signing"'* ]]
  logged "security list-keychains -d user -s $LOGIN $SIGNING/signing.keychain-db"
  logged "codesign -f -s 0123456789ABCDEF $TOOL"
  # restored on exit: the last list-keychains call is the one without our keychain
  [ "$(grep -F 'list-keychains -d user -s' "$FAKE_LOG" | tail -1)" = "security list-keychains -d user -s $LOGIN" ]
  [ ! -d "$SIGNING/.lock" ]
}

@test "codesign --has-identity: yes only when the keychain opens and holds the certificate" {
  run bash "$REPO/app/tools/codesign.sh" --has-identity
  [ "$status" -eq 0 ]                                          # setup: a keychain with the certificate
  not_logged "codesign -"                                      # a question: nothing signed
  FAKE_UNLOCK_FAIL=1 run bash "$REPO/app/tools/codesign.sh" --has-identity
  [ "$status" -ne 0 ]                                          # the password no longer opens it
  FAKE_IDENTITY="" run bash "$REPO/app/tools/codesign.sh" --has-identity
  [ "$status" -ne 0 ]                                          # no certificate in it
  rm "$SIGNING/keychain-password"
  run bash "$REPO/app/tools/codesign.sh" --has-identity
  [ "$status" -ne 0 ]                                          # no password file
}
