"""launchd jobs (schedule.py) and what Chrome's "Install page as app" reads (manifest, icons, theme color).

Never touches the real ~/Library/LaunchAgents or launchctl: Path.home() points at a temp folder and launchctl is a
recorder.
"""

import json
import plistlib
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fintrack import schedule


@pytest.fixture
def launchd(tmp_path, monkeypatch):
    """A temp home for the plists and a fake launchctl that records its calls."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    calls = []

    def fake_launchctl(*args, check=True):
        calls.append(args)
        # `print` of a loaded job: the lines status() reads (nested sections repeat "state =").
        out = "\tstate = running\n\tpid = 4242\n\tlast exit code = (never exited)\n\t\tstate = active\n"
        return SimpleNamespace(returncode=0, stdout=out if args[0] == "print" else "")

    monkeypatch.setattr(schedule, "_launchctl", fake_launchctl)
    return calls


def test_serve_plist_runs_fintrack_serve_at_login(settings):
    plist = schedule.build_serve_plist(settings)
    assert plist["Label"] == "com.fintrack.serve"
    assert plist["ProgramArguments"][1:] == ["serve"]
    assert plist["ProgramArguments"][0].startswith(str(sys.prefix))  # the venv's command, not the repo
    assert plist["EnvironmentVariables"]["FINTRACK_HOME"] == str(settings.home)
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] == {"SuccessfulExit": False}  # restarted after a crash, not after a clean stop
    assert plist["ThrottleInterval"] >= 30
    assert plist["StandardOutPath"].startswith(str(settings.logs_dir))
    plistlib.dumps(plist)  # serializable


def test_both_jobs_create_files_mode_600(settings):
    for plist in (schedule.build_serve_plist(settings), schedule.build_plist(settings, [(6, 0)])):
        assert plist["Umask"] == 0o077


def test_install_restart_uninstall_serve(settings, launchd):
    path = schedule.install_serve(settings)
    assert path == Path.home() / "Library" / "LaunchAgents" / "com.fintrack.serve.plist"
    with open(path, "rb") as handle:
        assert plistlib.load(handle)["ProgramArguments"][1] == "serve"
    assert [c[0] for c in launchd] == ["bootout", "bootstrap"]  # an old version is unloaded first
    log = Path(schedule.build_serve_plist(settings)["StandardOutPath"])
    assert log.stat().st_mode & 0o777 == 0o600  # made before launchd opens it (launchd would make it 644)

    text = schedule.serve_status(settings)
    assert "launchd: loaded" in text and "state = running" in text and "pid = 4242" in text
    assert "state = active" not in text  # the job's own state, not a nested section's
    assert settings.base_url in text

    assert schedule.restart_serve() is True
    assert launchd[-1][:2] == ("kickstart", "-k") and launchd[-1][2].endswith("/com.fintrack.serve")

    assert schedule.uninstall_serve() is True
    assert not path.exists()
    assert schedule.serve_status(settings) == "Not installed."
    assert schedule.restart_serve() is False and schedule.uninstall_serve() is False


def test_serve_job_leaves_the_sync_job_alone(settings, launchd):
    schedule.install(settings, [(6, 0)])
    schedule.install_serve(settings)
    schedule.uninstall_serve()
    assert schedule.plist_path().exists()  # com.fintrack.sync still installed
    assert "Runs daily at: 06:00" in schedule.status()


# ---------------------------------------------------------------- Chrome's installed app

@pytest.fixture
def client(settings, conn, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


def test_manifest_and_icons_are_served(client):
    page = client.get("/").text
    href = re.search(r'<link rel="manifest" href="([^"]+)"', page).group(1)  # url_for: a full URL
    assert href.endswith("/static/manifest.webmanifest")
    response = client.get(href)
    assert response.status_code == 200
    manifest = json.loads(response.text)
    assert manifest["display"] == "standalone" and manifest["start_url"] == "/" and manifest["scope"] == "/"
    sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes  # what Chrome needs to install it
    # Android colours the installed app's gesture bar from this, fixed at install (no page can switch it): the dark
    # --rail, the phone's bottom icon bar (user's choice 2026-11-06, docs/ui.md Phones)
    assert manifest["theme_color"] == "#121211"
    for icon in manifest["icons"]:  # paths relative to the manifest
        icon_response = client.get("/static/" + icon["src"])
        assert icon_response.status_code == 200 and icon_response.headers["content-type"] == icon["type"]


def test_theme_color_follows_the_chosen_theme(client):
    both = client.get("/").text
    assert 'content="#ffffff" media="(prefers-color-scheme: light)"' in both
    assert 'content="#1f1f1d" media="(prefers-color-scheme: dark)"' in both
    client.cookies.set("ft_theme", "dark")
    dark = client.get("/").text
    assert '<meta name="theme-color" content="#1f1f1d" ' in dark and "prefers-color-scheme: light" not in dark
    # phones: the bars' --rail, listed first (the first matching tag wins), so the status bar and the installed app's
    # gesture bar match the top and bottom bars (2026-11-06)
    assert dark.index('content="#121211" media="(max-width: 640px)"') < dark.index('content="#1f1f1d" ')
    assert 'content="#eeeee9" media="(max-width: 640px) and (prefers-color-scheme: light)"' in both


# ---------------------------------------------------------------- the phone gate's job

def test_gate_plist_runs_the_gate_sandboxed(settings):
    import dataclasses

    gated = dataclasses.replace(settings, remote_access=True, remote_host="203.0.113.7")
    plist = schedule.build_gate_plist(gated)
    assert plist["Label"] == "com.fintrack.gate"
    assert plist["ProgramArguments"][0] == "/usr/bin/sandbox-exec"
    assert "-I" in plist["ProgramArguments"] and "-S" in plist["ProgramArguments"]
    assert "EnvironmentVariables" not in plist                  # Python -I ignores it anyway; nothing to leak
    assert plist["KeepAlive"] == {"SuccessfulExit": False} and plist["RunAtLoad"] is True
    assert plist["StandardOutPath"] == str(gated.logs_dir / "gate.log") and plist["Umask"] == 0o077
    plistlib.dumps(plist)


def test_install_restart_uninstall_gate(settings, launchd):
    import dataclasses

    gated = dataclasses.replace(settings, remote_access=True, remote_host="203.0.113.7")
    assert schedule.restart_gate() is False                     # not installed yet
    schedule.install_gate(gated)
    assert schedule.gate_installed()
    assert (gated.logs_dir / "gate.log").stat().st_mode & 0o777 == 0o600
    assert schedule.restart_gate() is True and launchd[-1][-1].endswith("/com.fintrack.gate")
    assert "Phones open: https://203.0.113.7:48443" in schedule.gate_status(gated)
    assert schedule.uninstall_gate() is True and not schedule.gate_installed()
