# dmgbuild settings for the disk image. build.sh passes the app, the background and the layout (the same numbers
# it gives make-dmg-background.swift, so the arrow sits between the icons):
#   dmgbuild -s dmg-settings.py -D app=... -D background=... -D width=660 -D height=400 -D app_x=170 \
#            -D apps_x=490 -D icons_y=190 claude-meeting <out.dmg>
import os.path

d = defines  # noqa: F821 (dmgbuild provides `defines`)
app = d["app"]
background = d["background"]
width, height = int(d["width"]), int(d["height"])

format = "UDRW"   # build.sh takes the marks off this writable image, then compresses it
files = [app]
symlinks = {"Applications": "/Applications"}
icon_locations = {
    os.path.basename(app): (int(d["app_x"]), int(d["icons_y"])),
    "Applications": (int(d["apps_x"]), int(d["icons_y"])),
}
window_rect = ((200, 160), (width, height))
icon_size = 112
text_size = 13
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
include_icon_view_settings = True
