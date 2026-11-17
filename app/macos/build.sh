#!/bin/bash
# Build "claude-meeting.app" into app/macos/build/ and sign it with the local certificate
# (tools/codesign.sh).
#   app/macos/build.sh             build the .app
#   app/macos/build.sh --install   ... and copy it to ~/Applications
#   app/macos/build.sh --dmg       ... and make build/claude-meeting-<version>.dmg to hand out
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$(cd "$HERE/.." && pwd)"
BUNDLE="$HERE/build/claude-meeting.app"
OLD_NAME="Meeting Copilot.app"                                   # before 0.1.17
VERSION="$(git -C "$HERE" describe --tags --always --dirty 2>/dev/null)" \
  || { echo "build.sh needs the git checkout: the version comes from its history" >&2; exit 1; }
[ ! -e "$(git -C "$HERE" rev-parse --git-dir)/shallow" ] \
  || { echo "shallow clone: run git fetch --unshallow so the build number counts every commit" >&2; exit 1; }
SHORT="${VERSION#v}"; SHORT="${SHORT%%-*}"                   # v0.1.0-3-gabc -> 0.1.0
case "$SHORT" in *[!0-9.]*|"") SHORT="0.0.0" ;; esac
BUILD="$(git -C "$HERE" rev-list --count HEAD)"               # CFBundleVersion: numbers only
DIRTY=""; [ "${VERSION%-dirty}" = "$VERSION" ] || DIRTY="-dirty"
ENGINE_VERSION="$BUILD.$(git -C "$HERE" rev-parse --short HEAD)$DIRTY"   # what the app compares with engine/.version
# The image of a release (a clean tree at a version tag) is built in a clone of the public repository: its build
# number and engine version then name commits anyone can look up. Any other image is one to test with.
if [ "${1:-}" = "--dmg" ] && [ "$VERSION" = "$(git -C "$HERE" describe --tags --exact-match 2>/dev/null)" ] \
   && [ "${MC_DMG_ANY_ORIGIN:-}" != 1 ]; then
  ORIGIN="$(git -C "$HERE" remote get-url origin 2>/dev/null || true)"
  [[ "$ORIGIN" =~ github\.com[:/]claude-meeting/claude-meeting(\.git)?/?$ ]] || {
    echo "$VERSION is a release: build its image in a clone of https://github.com/claude-meeting/claude-meeting" \
      "(origin here: ${ORIGIN:-none}), or set MC_DMG_ANY_ORIGIN=1 for an image to test with" >&2; exit 1; }
  # A release is signed with an identity of its own, not the one this Mac signs its everyday builds with: the
  # certificate in a published app can be compared with any other app signed here.
  RELEASE_SIGNER="${MEETING_COPILOT_SIGNING_NAME:-}"
  if [ -z "$RELEASE_SIGNER" ] || [ "$RELEASE_SIGNER" = "meeting-copilot local signing" ] \
     || [ -z "${MEETING_COPILOT_SIGNING_DIR:-}" ]; then
    echo "$VERSION is a release: sign it with its own identity (set MEETING_COPILOT_SIGNING_NAME and" \
      "MEETING_COPILOT_SIGNING_DIR, then run install.sh --no-service so the helpers are signed with it too)" >&2; exit 1
  fi
  # The engine folders are copied whole: in a release only tracked files and the helpers install.sh builds go in.
  EXTRA="$(git -C "$APP_DIR/.." status --porcelain --ignored -uall -- app/bin app/lib app/src app/tools \
    | grep -v -E '/__pycache__/|^!! app/bin/(mic-users|screen-ocr|frames-to-video)$|^!! app/bin/MeetingCopilotScreen\.app/' \
    || true)"
  [ -z "$EXTRA" ] || { echo "$VERSION is a release: the engine folders hold files that are not part of it:" >&2
    echo "$EXTRA" >&2; exit 1; }
fi
command -v uv >/dev/null || { echo "uv is missing (brew install uv): it is bundled into the app" >&2; exit 1; }

cd "$HERE"
swift build -c release
rm -rf "$BUNDLE"
rm -rf "$HERE/build/$OLD_NAME"
# en.lproj stays, empty: it tells macOS the bundle speaks English, so a Mac with Chinese second (or another
# language first) gets the English permission prompts from Info.plist, not one of the Chinese tables.
mkdir -p "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources/zh-Hant.lproj" "$BUNDLE/Contents/Resources/zh-Hans.lproj" \
  "$BUNDLE/Contents/Resources/en.lproj"
cp "$(swift build -c release --show-bin-path)/MeetingCopilot" "$BUNDLE/Contents/MacOS/MeetingCopilot"
# A release build keeps a debug map that names every source and object file by its full path on this Mac
# (the account name included). Strip it, and the local symbols, before signing.
strip -S -x "$BUNDLE/Contents/MacOS/MeetingCopilot"
cat > "$BUNDLE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleIdentifier</key><string>com.meeting-copilot.app</string>
  <key>CFBundleName</key><string>claude-meeting</string>
  <key>CFBundleDisplayName</key><string>claude-meeting</string>
  <key>CFBundleExecutable</key><string>MeetingCopilot</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$SHORT</string>
  <key>CFBundleVersion</key><string>$BUILD</string>
  <key>MCEngineVersion</key><string>$ENGINE_VERSION</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>15.0</string>
  <key>NSHumanReadableCopyright</key><string>Apache License 2.0</string>
  <key>NSMicrophoneUsageDescription</key><string>Records 30 seconds of your voice once, to create your voiceprint.</string>
  <key>NSAppleEventsUsageDescription</key><string>Opens Terminal for the Claude sign-in and the live notes.</string>
</dict>
</plist>
PLIST
# The name is claude-meeting in every language; only the permission prompts are translated.
cat > "$BUNDLE/Contents/Resources/zh-Hant.lproj/InfoPlist.strings" <<'STRINGS'
NSMicrophoneUsageDescription = "只錄一次 30 秒你的聲音，用來建立聲紋。";
NSAppleEventsUsageDescription = "開啟終端機完成 Claude 登入，以及顯示即時筆記。";
STRINGS
cat > "$BUNDLE/Contents/Resources/zh-Hans.lproj/InfoPlist.strings" <<'STRINGS'
NSMicrophoneUsageDescription = "只录一次 30 秒你的声音，用来建立声纹。";
NSAppleEventsUsageDescription = "打开终端完成 Claude 登录，以及显示实时笔记。";
STRINGS
# The engine travels inside the app: its code, prebuilt helpers and uv. On first launch the app
# copies it to ~/Library/Application Support/Meeting Copilot/engine and runs install.sh there.
for tool in mic-users screen-ocr frames-to-video; do
  [ -x "$APP_DIR/bin/$tool" ] && ! [ "$APP_DIR/src/$tool.swift" -nt "$APP_DIR/bin/$tool" ] \
    || { echo "$tool is out of date: run install.sh first" >&2; exit 1; }
done
! [ "$APP_DIR/src/screen-capture.swift" -nt "$APP_DIR/bin/MeetingCopilotScreen.app/Contents/MacOS/screen-capture" ] \
  || { echo "screen-capture is out of date: run install.sh first" >&2; exit 1; }
# Its Info.plist as install.sh writes it now (the name System Settings lists): installs from the app keep this one.
"$APP_DIR/tools/screen-helper-plist.sh" "$APP_DIR/bin/MeetingCopilotScreen.app" check \
  || { echo "screen-capture's Info.plist is out of date: run install.sh first" >&2; exit 1; }
# Prebuilt helpers must run on macOS 15 (the app's minimum) and carry the stable signature.
for bin in "$APP_DIR/bin/mic-users" "$APP_DIR/bin/frames-to-video" "$APP_DIR/bin/screen-ocr" \
           "$APP_DIR/bin/MeetingCopilotScreen.app/Contents/MacOS/screen-capture"; do
  vtool -show-build "$bin" 2>/dev/null | grep -q 'minos 15' || { echo "$(basename "$bin") needs macOS > 15: rebuild with install.sh" >&2; exit 1; }
done
for bin in "$APP_DIR/bin/mic-users" "$APP_DIR/bin/screen-ocr" "$APP_DIR/bin/frames-to-video" "$APP_DIR/bin/MeetingCopilotScreen.app"; do
  codesign -dv "$bin" 2>&1 | grep -q 'Signature=adhoc' && { echo "$(basename "$bin") is ad-hoc signed: run install.sh" >&2; exit 1; }
done
ENGINE="$BUNDLE/Contents/Resources/engine"
mkdir -p "$ENGINE/app" "$ENGINE/bin"
cp -p "$APP_DIR/../install.sh" "$APP_DIR/../uninstall.sh" "$HERE/sync-engine.sh" "$ENGINE/"
rsync -a --exclude '__pycache__' --exclude '.DS_Store' "$APP_DIR/bin" "$APP_DIR/lib" "$APP_DIR/src" "$APP_DIR/tools" "$ENGINE/app/"
cp -p "$APP_DIR/config.example.toml" "$APP_DIR/requirements.txt" "$ENGINE/app/"
touch "$ENGINE/app/bin/.prebuilt"          # install.sh: use these signed helpers, never rebuild them
cp "$(readlink -f "$(command -v uv)")" "$ENGINE/bin/uv"
# License texts of what the app carries: this project's, the notices, and uv's own (Apache-2.0 or MIT), kept in
# app/macos/third-party/uv so the build does not depend on how uv was installed.
LICENSES="$BUNDLE/Contents/Resources/licenses"
mkdir -p "$LICENSES"
cp -p "$APP_DIR/../LICENSE" "$APP_DIR/../THIRD_PARTY_NOTICES.md" "$LICENSES/"
for f in LICENSE-APACHE LICENSE-MIT; do cp -p "$HERE/third-party/uv/$f" "$LICENSES/uv-$f"; done

ICON="$HERE/build/AppIcon.icns"
if [ ! -f "$ICON" ] || [ "$HERE/make-icon.swift" -nt "$ICON" ]; then
  rm -rf "$HERE/build/AppIcon.iconset"
  swift "$HERE/make-icon.swift" "$HERE/build/AppIcon.iconset" && iconutil -c icns "$HERE/build/AppIcon.iconset" -o "$ICON"
fi
cp "$ICON" "$BUNDLE/Contents/Resources/AppIcon.icns"

"$APP_DIR/tools/codesign.sh" "$BUNDLE"
if codesign -dv "$BUNDLE" 2>&1 | grep -q 'Signature=adhoc'; then
  echo "the app is ad-hoc signed (no local certificate): permissions would not survive updates" >&2; exit 1
fi
if [ -n "${RELEASE_SIGNER:-}" ]; then
  for item in "$BUNDLE" "$ENGINE/app/bin/mic-users" "$ENGINE/app/bin/screen-ocr" "$ENGINE/app/bin/frames-to-video" \
              "$ENGINE/app/bin/MeetingCopilotScreen.app"; do
    codesign -dvv "$item" 2>&1 | grep -q -x -F "Authority=$RELEASE_SIGNER" \
      || { echo "${item#"$BUNDLE/"} is not signed by \"$RELEASE_SIGNER\": run install.sh --no-service with it" >&2; exit 1; }
  done
fi
# No program in the app may name a folder of the Mac that built it. Ours are built here, so any folder under the users
# folder counts; uv is built by others (Homebrew's or a release runner's account shows in its panic paths), so in it
# only this account counts.
USERS_DIR=/Users
ME="$(id -un)"
LEAKS=""
while IFS= read -r -d '' f; do
  case "$(file -b "$f")" in Mach-O*) ;; *) continue ;; esac
  if [ "$f" = "$ENGINE/bin/uv" ]; then
    others="$(LC_ALL=C grep -a -o "$USERS_DIR/[A-Za-z0-9._-]*" "$f" | grep -x -F "$USERS_DIR/$ME" || true)"
  else
    others="$(LC_ALL=C grep -a -o "$USERS_DIR/[A-Za-z0-9._-]*" "$f" || true)"
  fi
  if [ -n "$others" ] || LC_ALL=C grep -a -q -F "$HOME/" "$f"; then
    LEAKS="$LEAKS ${f#"$BUNDLE/"}"
  fi
done < <(find "$BUNDLE" -type f -print0)
[ -z "$LEAKS" ] || { echo "paths of this Mac in:$LEAKS" >&2; exit 1; }
echo "built $BUNDLE"

if [ "${1:-}" = "--dmg" ]; then
  DMG="$HERE/build/claude-meeting-${VERSION#v}.dmg"
  rm -f "$DMG"
  # The window opens laid out: a background with an arrow, the app on the left, Applications on the right.
  # dmgbuild writes Finder's layout file itself, so no Finder (and no Automation permission) is needed. It runs
  # from its own environment, installed once from dmg-requirements.txt with every package's hash checked.
  GEOMETRY=(660 400 170 490 190)          # window width, height; x of the app and of Applications; y of both
  BG="$HERE/build/dmg-background"
  if [ ! -f "$BG/background.png" ] || [ ! -f "$BG/background@2x.png" ] \
     || [ "$HERE/make-dmg-background.swift" -nt "$BG/background@2x.png" ] \
     || [ "$(cat "$BG/geometry" 2>/dev/null)" != "${GEOMETRY[*]}" ]; then
    swift "$HERE/make-dmg-background.swift" "$BG" "${GEOMETRY[@]}" && echo "${GEOMETRY[*]}" > "$BG/geometry"
  fi
  DMGENV="$HERE/build/dmgbuild-venv"
  # Packages and hashes only: a change to the file's comments does not reinstall.
  if [ "$(grep -v '^ *#' "$HERE/dmg-requirements.txt")" != "$(grep -sv '^ *#' "$DMGENV/requirements.txt")" ]; then
    rm -rf "$DMGENV"
    uv venv --quiet --python 3.12 "$DMGENV" \
      && uv pip install --quiet --python "$DMGENV/bin/python" --require-hashes -r "$HERE/dmg-requirements.txt" \
      && cp "$HERE/dmg-requirements.txt" "$DMGENV/requirements.txt" \
      || { echo "could not install dmgbuild (needs the network the first time)" >&2; exit 1; }
  fi
  # The image gets a copy of the app without extended attributes: com.apple.provenance carries an id from this
  # Mac's policy database, com.apple.quarantine one of a download.
  STAGE="$HERE/build/dmg-stage"
  rm -rf "$STAGE" && mkdir -p "$STAGE"
  ditto --noextattr --noqtn "$BUNDLE" "$STAGE/$(basename "$BUNDLE")"
  xattr -cr "$STAGE" 2>/dev/null || true
  # In UTC: HFS+ keeps the volume's creation date in local time, which would give away the time zone.
  TZ=UTC LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 "$DMGENV/bin/dmgbuild" -s "$HERE/dmg-settings.py" \
    -D app="$STAGE/$(basename "$BUNDLE")" -D background="$BG/background.png" \
    -D width="${GEOMETRY[0]}" -D height="${GEOMETRY[1]}" -D app_x="${GEOMETRY[2]}" -D apps_x="${GEOMETRY[3]}" \
    -D icons_y="${GEOMETRY[4]}" "claude-meeting" "$DMG" >/dev/null || { echo "dmgbuild failed" >&2; exit 1; }
  rm -rf "$STAGE"
  # hdiutil names the partitions in the Mac's language ("disk image（Apple_HFS：4）" on a Chinese Mac) and reads no
  # locale variable for it. Write the names an English Mac writes, "disk image (Apple_HFS : 4)": they sit in the
  # image's XML resources, which no checksum covers.
  "$DMGENV/bin/python" -I - "$DMG" <<'PY' || { rm -f "$DMG"; echo "could not rename the partitions in $DMG" >&2; exit 1; }
import plistlib, re, struct, sys
with open(sys.argv[1], "r+b") as f:
    data = f.read()
    koly = bytearray(data[-512:])
    off, size = struct.unpack(">QQ", koly[0xD8:0xE8])  # the XML resources: right before the 512-byte trailer
    if koly[:4] != b"koly" or off + size != len(data) - 512:
        sys.exit("not the expected UDIF layout")
    res = plistlib.loads(data[off:off + size])
    for part in res["resource-fork"]["blkx"]:
        m = re.fullmatch(r"(.*?)\s*[（(]\s*(.+?)\s*[：:]\s*(\d+)\s*[）)]", part["CFName"])
        if not m:
            sys.exit(f"unexpected partition name {part['CFName']!r}")
        part["Name"] = part["CFName"] = "%s (%s : %s)" % m.groups()
    xml = plistlib.dumps(res)
    struct.pack_into(">Q", koly, 0xE0, len(xml))
    f.seek(off)
    f.write(xml + koly)
    f.truncate()
PY
  # dmgbuild does not check its copy of the app: the app inside must be whole and correctly signed.
  MNT="$(mktemp -d)"
  hdiutil attach -nobrowse -readonly -quiet -mountpoint "$MNT" "$DMG" || { echo "cannot open $DMG" >&2; exit 1; }
  ok=0; codesign --verify --deep --strict "$MNT/claude-meeting.app" 2>/dev/null || ok=$?
  # A shell started from an app macOS tracks (one downloaded from the web) gives every file it writes that app's
  # com.apple.provenance, and xattr cannot take it off: such an image is refused. -s reads the Applications link
  # itself: without it xattr reports the attributes of this Mac's /Applications folder.
  MARKS="$(xattr -rs "$MNT" 2>/dev/null | grep -c -E ': com\.apple\.(provenance|quarantine)$' || true)"
  hdiutil detach -quiet "$MNT"; rmdir "$MNT"
  [ $ok -eq 0 ] || { rm -f "$DMG"; echo "the app in the disk image is incomplete or its signature is broken" >&2; exit 1; }
  if [ "$MARKS" != 0 ] && [ "${MC_DMG_KEEP_PROVENANCE:-}" != 1 ]; then
    rm -f "$DMG"
    echo "the disk image carries com.apple.provenance or com.apple.quarantine ($MARKS times): run build.sh --dmg from" \
         "a shell whose new files have none (touch one, then xattr it), or set MC_DMG_KEEP_PROVENANCE=1 to keep them" >&2
    exit 1
  fi
  echo "disk image $DMG ($(du -h "$DMG" | cut -f1)): version $SHORT, build $BUILD, engine $ENGINE_VERSION"
fi

if [ "${1:-}" = "--install" ]; then
  mkdir -p "$HOME/Applications"
  rm -rf "$HOME/Applications/$(basename "$BUNDLE")" "$HOME/Applications/$OLD_NAME"   # the old name too
  cp -R "$BUNDLE" "$HOME/Applications/"
  # Leave macOS one copy to open: otherwise a click on a notification of the installed copy can start the copy
  # in the build folder, which macOS knows too, as a second app.
  LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister
  if [ -x "$LSREGISTER" ]; then
    "$LSREGISTER" -u "$BUNDLE" || true
    "$LSREGISTER" -f "$HOME/Applications/$(basename "$BUNDLE")" || true   # its name and icon as they are now
  fi
  echo "installed $HOME/Applications/$(basename "$BUNDLE")"
fi
