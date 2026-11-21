# sctui

A full-screen SoundCloud player for the terminal. It's built with
[Textual](https://textual.textualize.io/), streams through `mpv`, and uses
`yt-dlp` to talk to SoundCloud.

## Install

```sh
./install.sh   # installs `sc`, plus a "SoundCloud" app-launcher entry and icon
```

Launch it from the app launcher or run `sc`.

On Omarchy/Hyprland, append [`share/hypr-bindings.lua`](share/hypr-bindings.lua)
to `~/.config/hypr/bindings.lua`. That adds **Super+Shift+Alt+S**, which
launches sctui or focuses it if it's already open, plus the global hotkeys
below.

sctui also:

- **Works with media keys and the Omarchy bar.** It registers as an MPRIS
  player (`org.mpris.MediaPlayer2.sctui`).
- **Shows a desktop notification on each track change**, with cover art.

Requires `mpv` (Omarchy ships it).

## Keys

| Key | Action |
| --- | --- |
| `/` | Search (or paste a track / playlist / user URL), then Enter |
| `Esc` | Back to results |
| `Enter` | Play the track and queue the results after it, or open a playlist |
| `a` | Add the highlighted result (or a whole playlist) to the queue |
| `Tab` | Switch between results and queue |
| `d` / `Del` | Remove the highlighted track from the queue |
| `Space` | Play / pause |
| `n` / `p` | Next / previous |
| `←` / `→` | Seek -5s / +5s |
| `Shift+←` / `Shift+→` | Seek -30s / +30s |
| `,` / `.` | Seek -10s / +10s |
| `0`–`9` | Jump to 0%–90% of the track |
| Click the progress bar | Jump to that point |
| `-` / `+` | Volume |
| `f` | Your feed (loads on start when a token is set) |
| `l` / `s` | Your likes / your playlists |
| `v` | Show / hide the visualizer |
| `q` | Quit |

## Global hotkeys (Winamp style)

These work from any app, even on the lock screen. They come from
`share/hypr-bindings.lua`:

| Keys | Action |
| --- | --- |
| `Ctrl+Alt+Insert` | Play (starts sctui if it isn't running) |
| `Ctrl+Alt+Home` | Play / pause |
| `Ctrl+Alt+End` | Stop (pause and rewind) |
| `Ctrl+Alt+PgUp` / `PgDn` | Previous / next |
| `Ctrl+Alt+↑` / `↓` | Volume (shows a toast) |
| `Ctrl+Alt+←` / `→` | Seek -5s / +5s |

They call `sc ctl <command>`, which you can also use from scripts:
`play pause toggle stop next prev vol+ vol- seek+ seek- show`.
If several copies of sctui are open, the most recently started one gets the
commands.

## Your account

Create `~/.config/sctui/config.toml`:

```toml
username = "your-soundcloud-permalink"   # the part after soundcloud.com/
# Optional. Needed for private content and better quality with Go+.
# In your browser, open soundcloud.com while logged in, then go to DevTools →
# Application → Cookies and copy the `oauth_token` value.
oauth_token = "2-123456-..."
volume = 80
theme = "ansi-dark"   # follows your terminal / Omarchy colors
visualizer = true     # spectrum bars (needs pactl + parec, standard on Omarchy)
notifications = true  # "now playing" toast on track change
```

You can also set `SC_USERNAME` and `SC_OAUTH_TOKEN` in the environment.

With `oauth_token` set, your feed loads at startup. Feed, likes and playlists
then come straight from SoundCloud's API, so they include full details
(artist, length, artwork, reposts). Without a token, `username` alone is
enough for your public likes and playlists, loaded through yt-dlp.

## License

MIT. Not affiliated with SoundCloud.
