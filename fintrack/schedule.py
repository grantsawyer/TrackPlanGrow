"""launchd jobs on macOS: the scheduled sync, the web server at login, and the phone gate.

launchd (not cron) because when the Mac is asleep at a scheduled time, launchd
runs the job once it wakes, while cron skips it. launchd never wakes the Mac itself: a wake before a sync is macOS's
own schedule (`sudo pmset repeat wakeorpoweron ...`, README "Scheduling syncs"). Each job is a plist in
~/Library/LaunchAgents (outside the repo) that runs the venv's `fintrack` command.

    fintrack schedule install --times 06:30,18:30     com.fintrack.sync: `fintrack sync` at those times
    fintrack schedule status | uninstall
    fintrack autostart install                        com.fintrack.serve: `fintrack serve` at login, restarted if it crashes
    fintrack autostart status | restart | uninstall
    fintrack remote enable | disable                  com.fintrack.gate: the sandboxed phone gate (gate.py, remote.py)
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from fintrack.config import Settings

LABEL = "com.fintrack.sync"
SERVE_LABEL = "com.fintrack.serve"
GATE_LABEL = "com.fintrack.gate"
DEFAULT_TIMES = "06:30,18:30"
# macOS's own tool that holds the Mac awake while a command runs (absolute path: launchd jobs have a bare PATH).
CAFFEINATE = "/usr/bin/caffeinate"
# Files a job's process creates are mode 600, like everything in FINTRACK_HOME (its log: see _install).
UMASK = 0o077


def plist_path(label: str = LABEL) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def parse_times(text: str) -> list[tuple[int, int]]:
    """'06:00,18:30' -> [(6, 0), (18, 30)]."""
    times = []
    for part in text.split(","):
        hour, minute = part.strip().split(":")
        hour_i, minute_i = int(hour), int(minute)
        if not (0 <= hour_i < 24 and 0 <= minute_i < 60):
            raise ValueError(f"Invalid time {part!r}; use HH:MM, 24-hour")
        times.append((hour_i, minute_i))
    if not times:
        raise ValueError("At least one time is required")
    return times


def _executable() -> str:
    # The fintrack command inside the venv in FINTRACK_HOME (outside the repo).
    return str(Path(sys.executable).with_name("fintrack"))


def build_plist(settings: Settings, times: list[tuple[int, int]]) -> dict:
    """The scheduled sync: `fintrack sync` at each time.

    It runs under `caffeinate -i`, which keeps the Mac from idle-sleeping until the sync ends: after a scheduled wake
    a closed laptop goes back to sleep within a minute or two, which could cut a sync off halfway. caffeinate holds
    nothing once the sync exits."""
    return {
        "Label": LABEL,
        "ProgramArguments": [CAFFEINATE, "-i", _executable(), "sync"],
        "EnvironmentVariables": {"FINTRACK_HOME": str(settings.home)},
        "StartCalendarInterval": [{"Hour": h, "Minute": m} for h, m in times],
        "StandardOutPath": str(settings.logs_dir / "sync-scheduled.log"),
        "StandardErrorPath": str(settings.logs_dir / "sync-scheduled.log"),
        "Umask": UMASK,
        "ProcessType": "Background",  # low CPU/IO priority; it's not time-critical
    }


def build_serve_plist(settings: Settings) -> dict:
    """The web server at login: `fintrack serve` (which backs up and migrates first, as from a terminal).

    KeepAlive restarts it only after a crash, not after a clean stop (launchctl bootout / uninstall); the throttle
    keeps a server that can't start (no certificate, port taken) from retrying more than every 30 seconds.
    """
    return {
        "Label": SERVE_LABEL,
        "ProgramArguments": [_executable(), "serve"],
        "EnvironmentVariables": {"FINTRACK_HOME": str(settings.home)},
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "StandardOutPath": str(settings.logs_dir / "serve-launchd.log"),
        "StandardErrorPath": str(settings.logs_dir / "serve-launchd.log"),
        "Umask": UMASK,
    }


def build_gate_plist(settings: Settings) -> dict:
    """The phone gate: gate.py under sandbox-exec (remote.gate_command), at login and after a crash.

    No EnvironmentVariables: the gate runs Python with -I -S (no environment, no venv) and gets everything as
    arguments. Its log is the one file it writes to, through the descriptor launchd opens (the sandbox allows no
    file writes)."""
    from fintrack import remote

    return {
        "Label": GATE_LABEL,
        "ProgramArguments": remote.gate_command(settings),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "StandardOutPath": str(settings.logs_dir / "gate.log"),
        "StandardErrorPath": str(settings.logs_dir / "gate.log"),
        "Umask": UMASK,
    }


def _launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=check)


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _install(plist: dict) -> Path:
    path = plist_path(plist["Label"])
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unload any previous version first so the new definition takes effect.
    _launchctl("bootout", _domain(), str(path), check=False)
    with open(path, "wb") as handle:
        plistlib.dump(plist, handle)
    # launchd opens the log itself, before Umask applies (seen 2026-11-05: a new log came out 644), and appends to an
    # existing file keeping its mode: so create it first, mode 600, and tighten an older one.
    for log in {plist["StandardOutPath"], plist["StandardErrorPath"]}:
        Path(log).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        Path(log).touch(mode=0o600)
        os.chmod(log, 0o600)
    _launchctl("bootstrap", _domain(), str(path))
    return path


def _uninstall(label: str) -> bool:
    path = plist_path(label)
    if not path.exists():
        return False
    _launchctl("bootout", _domain(), str(path), check=False)
    path.unlink()
    return True


def _status(label: str, describe) -> str:
    """Installed / loaded / last exit and the log, plus describe(plist)'s lines for what the job does."""
    path = plist_path(label)
    if not path.exists():
        return "Not installed."
    with open(path, "rb") as handle:
        plist = plistlib.load(handle)
    loaded = _launchctl("print", f"{_domain()}/{label}", check=False)
    state = "loaded" if loaded.returncode == 0 else "NOT loaded (try reinstalling)"
    found: dict[str, str] = {}  # the first of each line: the job's own (nested sections repeat "state = ")
    for line in loaded.stdout.splitlines():
        for key in ("last exit code", "state = ", "pid = "):
            if key in line:
                found.setdefault(key, line.strip())
    lines = [f"Installed: {path}", *describe(plist), f"launchd: {state}"]
    lines += [f"launchd {found[key]}" for key in ("state = ", "pid = ", "last exit code") if key in found]
    lines.append(f"Log: {plist['StandardOutPath']}")
    return "\n".join(lines)


# --- the scheduled sync

def install(settings: Settings, times: list[tuple[int, int]]) -> Path:
    return _install(build_plist(settings, times))


def uninstall() -> bool:
    return _uninstall(LABEL)


def status() -> str:
    return _status(LABEL, lambda plist: ["Runs daily at: " + ", ".join(
        f"{t['Hour']:02d}:{t['Minute']:02d}" for t in plist["StartCalendarInterval"])])


# --- the web server at login

def install_serve(settings: Settings) -> Path:
    """Install and start now (RunAtLoad), so the app is up without logging out."""
    return _install(build_serve_plist(settings))


def uninstall_serve() -> bool:
    """Stop the server and remove the job."""
    return _uninstall(SERVE_LABEL)


def restart_serve() -> bool:
    """Restart the running server (after a code change, or a new .env): kill it and start it again at once."""
    if not plist_path(SERVE_LABEL).exists():
        return False
    _launchctl("kickstart", "-k", f"{_domain()}/{SERVE_LABEL}")
    return True


def serve_status(settings: Settings) -> str:
    return _status(SERVE_LABEL, lambda plist: [f"Starts at login: {settings.base_url}"])


# --- the phone gate

def install_gate(settings: Settings) -> Path:
    """Install and start the gate now (RunAtLoad); reinstalling restarts it with the current phones."""
    return _install(build_gate_plist(settings))


def uninstall_gate() -> bool:
    return _uninstall(GATE_LABEL)


def gate_installed() -> bool:
    return plist_path(GATE_LABEL).exists()


def restart_gate() -> bool:
    """Restart a running gate (after a phone is added or removed: it reads clients.pem only at start)."""
    if not gate_installed():
        return False
    _launchctl("kickstart", "-k", f"{_domain()}/{GATE_LABEL}")
    return True


def gate_status(settings: Settings) -> str:
    return _status(GATE_LABEL, lambda plist: [f"Phones open: {settings.remote_origin}"])
