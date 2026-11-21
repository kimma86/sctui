"""Entry point for `sc`. `sc ctl <command>` is kept import-light so global hotkeys feel instant."""

import subprocess
import sys

COMMANDS = ["play", "pause", "toggle", "stop", "next", "prev", "vol+", "vol-", "seek+", "seek-", "show"]
LAUNCH = ["omarchy-launch-or-focus-tui", "--app-id=org.omarchy.sctui", "sc"]

USAGE = f"""usage: sc                 open the player
       sc ctl <command>   control a running sctui ({", ".join(COMMANDS)})"""


def ctl(command: str) -> int:
    if command not in COMMANDS:
        print(USAGE, file=sys.stderr)
        return 2
    r = subprocess.run(
        ["busctl", "--user", "call", "org.mpris.MediaPlayer2.sctui", "/org/mpris/MediaPlayer2",
         "org.sctui.Control", "Command", "s", command],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        return 0
    not_running = any(m in r.stderr for m in ("not activatable", "ServiceUnknown", "not provided by any .service"))
    if not_running and command in ("play", "toggle"):  # Play starts it, like Winamp
        subprocess.Popen(LAUNCH, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return 0
    if not_running:
        print("sctui isn't running", file=sys.stderr)
    else:
        print(f"sctui didn't accept '{command}' (an older version may be running — restart it): "
              + r.stderr.strip(), file=sys.stderr)
    return 1


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "ctl":
        sys.exit(ctl(args[1] if len(args) > 1 else ""))
    if args and args[0] in ("-h", "--help"):
        print(USAGE)
        return
    from .app import main as run_app

    run_app()
