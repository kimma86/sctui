-- sctui (terminal SoundCloud player): launch, or focus it if already open.
o.bind("SUPER + SHIFT + ALT + S", "SoundCloud", "omarchy-launch-or-focus-tui --app-id=org.omarchy.sctui sc")

-- sctui global hotkeys, Winamp style (work from any app, even on the lock screen).
local sc = os.getenv("HOME") .. "/.local/bin/sc ctl "
o.bind("CTRL + ALT + Insert",    "sctui: Play",        sc .. "play",  { locked = true })
o.bind("CTRL + ALT + Home",      "sctui: Play/Pause",  sc .. "toggle", { locked = true })
o.bind("CTRL + ALT + End",       "sctui: Stop",        sc .. "stop",  { locked = true })
o.bind("CTRL + ALT + Page_Up",   "sctui: Previous",    sc .. "prev",  { locked = true })
o.bind("CTRL + ALT + Page_Down", "sctui: Next",        sc .. "next",  { locked = true })
o.bind("CTRL + ALT + Up",        "sctui: Volume up",   sc .. "vol+",  { locked = true, repeating = true })
o.bind("CTRL + ALT + Down",      "sctui: Volume down", sc .. "vol-",  { locked = true, repeating = true })
o.bind("CTRL + ALT + Left",      "sctui: Rewind 5s",   sc .. "seek-", { locked = true, repeating = true })
o.bind("CTRL + ALT + Right",     "sctui: Forward 5s",  sc .. "seek+", { locked = true, repeating = true })
