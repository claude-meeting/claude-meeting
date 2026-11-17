#!/bin/bash
# Sign a helper (binary or .app) with a stable local identity, so macOS permissions such as
# Screen Recording survive rebuilds. An ad-hoc signature changes with every build and macOS
# then forgets the grant; a certificate signature keeps the same identity.
#
# The certificate is self-signed and lives in its own keychain file
# (~/.config/meeting-copilot/signing/), never in the login keychain. That keychain is put on the
# search list only while signing, then the list is restored. Falls back to ad-hoc signing.
#
# Usage: codesign.sh <path> [<path> ...]
set -uo pipefail

DIR="${MEETING_COPILOT_SIGNING_DIR:-$HOME/.config/meeting-copilot/signing}"   # overridable for tests
KC="$DIR/signing.keychain-db"
PWFILE="$DIR/keychain-password"
NAME="${MEETING_COPILOT_SIGNING_NAME:-meeting-copilot local signing}"   # a release uses its own (build.sh)
# codesign.sh --has-identity: exit 0 when signing would use this identity (the keychain unlocks with its password
# file and holds the certificate), so a caller can avoid a re-sign that would reset Screen Recording.
if [ "${1:-}" = --has-identity ]; then
  [ -f "$KC" ] && [ -f "$PWFILE" ] && security unlock-keychain -p "$(cat "$PWFILE")" "$KC" 2>/dev/null \
    && security find-identity -p codesigning "$KC" 2>/dev/null | grep -qF "\"$NAME\""
  exit $?
fi
OPENSSL=/usr/bin/openssl     # LibreSSL: writes PKCS#12 files the macOS keychain can import

adhoc() {
  local ok=1
  for p in "$@"; do codesign -s - -f "$p" 2>/dev/null || ok=0; done
  [ $ok = 1 ] && echo "  signed ad-hoc (permissions reset when rebuilt)" || echo "  ! ad-hoc signing failed"
}

create_identity() {
  mkdir -p "$DIR" && chmod 700 "$DIR"
  # A keychain without its password file (or one that no longer unlocks) is useless: move it aside.
  [ ! -f "$KC" ] || mv "$KC" "$KC.old.$(date +%s)"
  local pw tmp
  pw="$($OPENSSL rand -hex 16)" || return 1
  tmp="$(mktemp -d)"
  ( umask 077; printf '%s' "$pw" > "$PWFILE" )
  security create-keychain -p "$pw" "$KC" >/dev/null 2>&1 || return 1
  security set-keychain-settings "$KC"                   # no auto-lock
  cat > "$tmp/cert.cnf" <<EOF
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = $NAME
[ext]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = critical,codeSigning
EOF
  $OPENSSL req -x509 -newkey rsa:2048 -nodes -days 7300 -config "$tmp/cert.cnf" \
    -keyout "$tmp/key.pem" -out "$tmp/cert.pem" 2>/dev/null &&
  $OPENSSL pkcs12 -export -inkey "$tmp/key.pem" -in "$tmp/cert.pem" -out "$tmp/id.p12" -passout pass:"$pw" &&
  security import "$tmp/id.p12" -k "$KC" -P "$pw" -T /usr/bin/codesign >/dev/null &&
  security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$pw" "$KC" >/dev/null
  local ok=$?
  rm -rf "$tmp"
  [ $ok -eq 0 ] && echo "  created signing certificate \"$NAME\" in $KC"
  return $ok
}

[ $# -gt 0 ] || { sed -n '2,10p' "$0"; exit 2; }
if [ ! -f "$KC" ] || [ ! -f "$PWFILE" ]; then
  create_identity || { echo "  ! could not create a signing certificate"; adhoc "$@"; exit 0; }
fi
# One signing at a time: the keychain search list is shared state.
LOCK="$DIR/.lock"
for _ in $(seq 1 100); do mkdir "$LOCK" 2>/dev/null && break; sleep 0.2; done
unlock() { security unlock-keychain -p "$(cat "$PWFILE")" "$KC" 2>/dev/null; }
if ! unlock; then
  create_identity && unlock || { rmdir "$LOCK" 2>/dev/null; adhoc "$@"; exit 0; }
fi
hash="$(security find-identity -p codesigning "$KC" | awk -v n="\"$NAME\"" 'index($0, n) {print $2; exit}')"
[ -n "$hash" ] || { rmdir "$LOCK" 2>/dev/null; echo "  ! signing certificate not found in $KC"; adhoc "$@"; exit 0; }

# Put our keychain on the search list just for codesign, and always restore the list.
orig=()
while IFS= read -r line; do
  line="${line#"${line%%[![:space:]]*}"}"; line="${line%\"}"; line="${line#\"}"
  [ -z "$line" ] || [ "$line" = "$KC" ] || orig+=("$line")      # never keep our own on the list
done < <(security list-keychains -d user)
if [ "${#orig[@]}" -eq 0 ]; then
  # The list could not be read: changing it could leave the login keychain off it. Sign ad-hoc instead.
  rmdir "$LOCK" 2>/dev/null; echo "  ! keychain search list unreadable"; adhoc "$@"; exit 0
fi
restore() { security list-keychains -d user -s "${orig[@]}"; rmdir "$LOCK" 2>/dev/null; }
trap restore EXIT
security list-keychains -d user -s "${orig[@]}" "$KC"

status=0
for p in "$@"; do
  codesign -f -s "$hash" "$p" 2>/dev/null || { codesign -s - -f "$p" 2>/dev/null; status=1; }
done
[ $status -eq 0 ] && echo "  signed with \"$NAME\" (permissions survive rebuilds)" \
                  || echo "  ! some files were signed ad-hoc instead"
exit 0
