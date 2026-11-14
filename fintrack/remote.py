"""Phone access away from home, with no app on the phone and no outside service (user's decision 2026-11-05).

    phone browser --TLS 1.3 + phone's client certificate--> gate (gate.py, sandboxed, REMOTE_PORT)
                  --plain, on this Mac only--> fintrack's phone listener (127.0.0.1:REMOTE_INNER_PORT)
                  --> passcode (30-day cookie) --> fintrack's pages; bank connections refused (web/remote.py)

Your router forwards REMOTE_PORT (TCP) to this Mac. The gate (its own process, under `sandbox-exec`: it can read Python
and its certificate files, listen on its port and connect to 127.0.0.1:REMOTE_INNER_PORT, nothing else) refuses every
connection without a phone's client certificate during the TLS handshake, before any of fintrack's code runs. fintrack
itself still listens on loopback only.

Certificates, all made here; every authority signs once and its private key is discarded unsaved, so nothing can ever
use it to sign another certificate:
  * the server certificate for REMOTE_HOST, from a one-off authority that each phone installs (ca.crt);
  * one client certificate per phone, from that phone's own one-off authority; the gate trusts the phones' authorities
    (clients.pem), so removing a phone removes its authority and cuts it off. A phone's private key travels once, in a
    password-protected file, and is stored nowhere.

Files in FINTRACK_HOME/remote/ (folder 700, files 600): cert.pem, key.pem, ca.crt, clients.pem, state.json (the
phones: name, date, authority certificate), passcode (scrypt hash), session.key (signs the cookies), gate.sb (the
sandbox profile).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import http.server
import ipaddress
import json
import logging
import os
import secrets
import shutil
import socket
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from fintrack.config import Settings

log = logging.getLogger(__name__)

# iOS rejects TLS certificates valid for more than 825 days (Apple, iOS 13+); stay under it.
CERT_DAYS = 820
RENEW_BEFORE_DAYS = 30
SESSION_DAYS = 30
SESSION_COOKIE = "ft_remote"
# 10 since 2026-11-11 (user's decision, with the phone's read-only copy): a passcode already set keeps working.
MIN_PASSCODE = 10
# Passcode attempts: after this many wrong ones in the window, refuse for the window (all phones; one user).
MAX_FAILURES = 5
FAILURE_WINDOW_S = 15 * 60
# Handing a phone its files: a one-time page on the home network, open this long at most.
OFFER_S = 10 * 60
OFFER_PORT = 8445
# Generic names: scanners record the server certificate, so it doesn't say "fintrack" or "finance".
AUTHORITY_NAME = "Private authority {date} (one-off)"
SERVER_NAME = "Private server"
GATE_SCRIPT = Path(__file__).with_name("gate.py")


class RemoteError(Exception):
    """A setup problem to show the user (no address yet, unknown phone, short passcode...)."""


# --------------------------------------------------------------------------- files

def remote_dir(settings: Settings) -> Path:
    return settings.home / "remote"


def _path(settings: Settings, name: str) -> Path:
    return remote_dir(settings) / name


def _write_private(path: Path, data: bytes | str) -> None:
    """Write a file mode 600 in a folder mode 700, like everything in FINTRACK_HOME."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    raw = data.encode() if isinstance(data, str) else data
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
    os.chmod(path, 0o600)


def cert_file(settings: Settings) -> Path:
    return _path(settings, "cert.pem")


def key_file(settings: Settings) -> Path:
    return _path(settings, "key.pem")


def ca_file(settings: Settings) -> Path:
    return _path(settings, "ca.crt")


def clients_file(settings: Settings) -> Path:
    return _path(settings, "clients.pem")


def profile_file(settings: Settings) -> Path:
    return _path(settings, "gate.sb")


def load_state(settings: Settings) -> dict:
    path = _path(settings, "state.json")
    return json.loads(path.read_text()) if path.exists() else {"phones": []}


def _save_state(settings: Settings, state: dict) -> None:
    _write_private(_path(settings, "state.json"), json.dumps(state, indent=2) + "\n")


def phones(settings: Settings) -> list[dict]:
    """Your phones: name, added (date), authority (its one-off authority's certificate, PEM)."""
    return load_state(settings)["phones"]


# --------------------------------------------------------------------------- certificates

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _host_name(host: str) -> x509.GeneralName:
    try:
        return x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        return x509.DNSName(host)


def _key_usage(*, cert_sign: bool) -> x509.KeyUsage:
    return x509.KeyUsage(digital_signature=not cert_sign, content_commitment=False, key_encipherment=False,
                         data_encipherment=False, key_agreement=False, key_cert_sign=cert_sign, crl_sign=cert_sign,
                         encipher_only=False, decipher_only=False)


def _one_off_authority(constraints: x509.NameConstraints | None) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    """A self-signed authority that may sign end certificates only (path length 0). The caller discards its key."""
    now = _now()
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, AUTHORITY_NAME.format(date=f"{now:%Y-%m-%d}"))])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(hours=1)).not_valid_after(now + timedelta(days=CERT_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(_key_usage(cert_sign=True), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
    )
    if constraints is not None:
        builder = builder.add_extension(constraints, critical=True)
    return key, builder.sign(key, hashes.SHA256())


def _end_certificate(authority_key, authority: x509.Certificate, subject: str, sans: list[x509.GeneralName] | None,
                     usage) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    now = _now()
    key = ec.generate_private_key(ec.SECP256R1())
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
        .issuer_name(authority.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(hours=1)).not_valid_after(now + timedelta(days=CERT_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(_key_usage(cert_sign=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)        # iOS requires serverAuth on servers
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(authority_key.public_key()), critical=False)
    )
    if sans:
        builder = builder.add_extension(x509.SubjectAlternativeName(sans), critical=False)
    return key, builder.sign(authority_key, hashes.SHA256())


def make_server_certificate(settings: Settings) -> Path:
    """The gate's certificate for REMOTE_HOST, from a one-off authority whose key never leaves this function.

    Nothing (not even this Mac, if it were compromised) can use that authority to sign another certificate phones
    would trust. Its name constraint (REMOTE_HOST only; other DNS names only under the reserved .invalid) is a second
    layer for browsers that enforce it. A new certificate means a new authority installed on each phone again.
    """
    if not settings.remote_host:
        raise RemoteError("Set your home's public address first: fintrack remote setup --host 203.0.113.7")
    name = _host_name(settings.remote_host)
    if isinstance(name, x509.IPAddress):
        permitted = [x509.IPAddress(ipaddress.ip_network(f"{name.value}/{name.value.max_prefixlen}")),
                     x509.DNSName("invalid")]
    else:
        permitted = [x509.DNSName(settings.remote_host)]
    authority_key, authority = _one_off_authority(x509.NameConstraints(permitted_subtrees=permitted,
                                                                       excluded_subtrees=None))
    # The address goes in the subject alternative name (what browsers check). The common name is plain words: OpenSSL
    # tests a hostname-like common name against the DNS constraint, and an IP there fails it ("permitted subtree
    # violation", seen 2026-11-05); it's also more generic for scanners.
    server_key, server = _end_certificate(authority_key, authority, SERVER_NAME, [name],
                                          ExtendedKeyUsageOID.SERVER_AUTH)
    del authority_key  # never saved: see the docstring
    _write_private(cert_file(settings), server.public_bytes(serialization.Encoding.PEM))
    _write_private(key_file(settings), server_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    _write_private(ca_file(settings), authority.public_bytes(serialization.Encoding.PEM))
    return cert_file(settings)


def certificate_expires(settings: Settings) -> datetime | None:
    """When the gate's certificate expires; None when there is none or it's for another address."""
    if not (cert_file(settings).exists() and key_file(settings).exists() and ca_file(settings).exists()):
        return None
    cert = x509.load_pem_x509_certificate(cert_file(settings).read_bytes())
    sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    if not settings.remote_host or _host_name(settings.remote_host) not in sans:
        return None
    return cert.not_valid_after_utc


def fingerprint(pem: bytes) -> str:
    """SHA-256 of a certificate, to compare with what the phone shows when installing it."""
    return x509.load_pem_x509_certificate(pem).fingerprint(hashes.SHA256()).hex(":").upper()


def _rebuild_clients(settings: Settings) -> None:
    """clients.pem: the authorities of the phones you have now (what the gate trusts)."""
    _write_private(clients_file(settings), "".join(p["authority"] for p in phones(settings)))


def add_phone(settings: Settings, name: str) -> tuple[bytes, str]:
    """A client certificate for this phone, from its own one-off authority. Returns the phone's file (.p12) and its
    password; neither is stored. A phone added again under the same name gets a new certificate, the old one stops.
    """
    name = name.strip()
    if not name or len(name) > 40 or any(c in name for c in "\n/\\\"'"):
        raise RemoteError("Name the phone in a few words, e.g. pixel")
    if certificate_expires(settings) is None:
        raise RemoteError("Run `fintrack remote setup --host <your home's public IP>` first.")
    authority_key, authority = _one_off_authority(None)
    phone_key, phone_cert = _end_certificate(authority_key, authority, name, None, ExtendedKeyUsageOID.CLIENT_AUTH)
    del authority_key  # never saved: this phone's certificate is the only one it will ever sign
    password = secrets.token_urlsafe(9)
    # Legacy PKCS#12 encryption (3DES / SHA-1 MAC): the one every Android and iOS version imports.
    encryption = (serialization.PrivateFormat.PKCS12.encryption_builder()
                  .kdf_rounds(50000)
                  .key_cert_algorithm(pkcs12.PBES.PBESv1SHA1And3KeyTripleDESCBC)
                  .hmac_hash(hashes.SHA1())
                  .build(password.encode()))
    p12 = pkcs12.serialize_key_and_certificates(name.encode(), phone_key, phone_cert, [authority], encryption)
    state = load_state(settings)
    state["phones"] = [p for p in state["phones"] if p["name"] != name] + [{
        "name": name, "added": datetime.now().date().isoformat(),
        "authority": authority.public_bytes(serialization.Encoding.PEM).decode(),
    }]
    _save_state(settings, state)
    _rebuild_clients(settings)
    return p12, password


def remove_phone(settings: Settings, name: str) -> bool:
    state = load_state(settings)
    kept = [p for p in state["phones"] if p["name"] != name]
    if len(kept) == len(state["phones"]):
        return False
    state["phones"] = kept
    _save_state(settings, state)
    _rebuild_clients(settings)
    return True


def setup(settings: Settings) -> Path:
    """Make the gate's certificate (when missing, for another address, or in its last 30 days) and its profile."""
    expires = certificate_expires(settings)
    if expires is None or expires < _now() + timedelta(RENEW_BEFORE_DAYS):
        make_server_certificate(settings)
    if not clients_file(settings).exists():
        _rebuild_clients(settings)
    return write_gate_profile(settings)


# --------------------------------------------------------------------------- passcode and sessions

_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def set_passcode(settings: Settings, passcode: str) -> None:
    """Store the passcode's scrypt hash and start a new session key, which signs every phone out."""
    if len(passcode) < MIN_PASSCODE:
        raise RemoteError(f"Use at least {MIN_PASSCODE} characters.")
    salt = os.urandom(16)
    digest = hashlib.scrypt(passcode.encode(), salt=salt, **_SCRYPT)
    _write_private(_path(settings, "passcode"), f"scrypt${_b64(salt)}${_b64(digest)}\n")
    _write_private(_path(settings, "session.key"), secrets.token_bytes(32))


def passcode_set(settings: Settings) -> bool:
    return _path(settings, "passcode").exists() and _path(settings, "session.key").exists()


def check_passcode(settings: Settings, passcode: str) -> bool:
    if not passcode_set(settings):
        return False
    _, salt, digest = _path(settings, "passcode").read_text().strip().split("$")
    attempt = hashlib.scrypt(passcode.encode(), salt=base64.b64decode(salt), **_SCRYPT)
    return hmac.compare_digest(attempt, base64.b64decode(digest))


def _sign(settings: Settings, text: str) -> str:
    key = _path(settings, "session.key").read_bytes()
    return hmac.new(key, text.encode(), hashlib.sha256).hexdigest()


def new_session(settings: Settings, now: float | None = None) -> str:
    """A cookie value: its expiry and a signature over it."""
    expires = int((now or time.time()) + SESSION_DAYS * 86400)
    return f"{expires}.{_sign(settings, str(expires))}"


def valid_session(settings: Settings, token: str | None, now: float | None = None) -> bool:
    if not token or "." not in token or not passcode_set(settings):
        return False
    expires, signature = token.split(".", 1)
    if not expires.isdigit() or int(expires) < (now or time.time()):
        return False
    return hmac.compare_digest(signature, _sign(settings, expires))


class Throttle:
    """Wrong passcodes: after MAX_FAILURES in FAILURE_WINDOW_S, refuse until the oldest of them ages out."""

    def __init__(self) -> None:
        self._failures: list[float] = []
        self._lock = threading.Lock()

    def _recent(self, now: float) -> list[float]:
        self._failures = [t for t in self._failures if now - t < FAILURE_WINDOW_S]
        return self._failures

    def locked(self, now: float | None = None) -> bool:
        with self._lock:
            return len(self._recent(now or time.time())) >= MAX_FAILURES

    def fail(self, now: float | None = None) -> None:
        with self._lock:
            now = now or time.time()
            self._recent(now).append(now)

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()


# --------------------------------------------------------------------------- the gate's sandbox

def python_executable() -> str:
    """The real Python binary (not the venv's symlink), which the gate runs with -I -S: the standard library only."""
    return os.path.realpath(getattr(sys, "_base_executable", sys.executable))


def _sb_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def gate_profile(settings: Settings) -> str:
    """The gate's sandbox (Apple's SBPL, run by /usr/bin/sandbox-exec): deny everything, then allow only what it needs.

    Reads: Python itself and its standard library, OpenSSL (Homebrew), the gate script and its three certificate
    files. Network: listen on REMOTE_PORT, connect to 127.0.0.1:REMOTE_INNER_PORT. Nothing else: no FINTRACK_HOME data,
    no home folder, no Keychain, no writing files (its log is a file descriptor launchd opened before it started).
    """
    python = python_executable()
    framework = python.split("/bin/")[0]                      # .../Python.framework/Versions/3.12
    openssl = os.path.realpath("/opt/homebrew/opt/openssl@3")
    reads = [f"(subpath {_sb_string(framework)})", f"(subpath {_sb_string(openssl)})",
             f"(subpath {_sb_string('/opt/homebrew/etc/openssl@3')})",
             f"(literal {_sb_string(str(GATE_SCRIPT))})"]
    reads += [f"(literal {_sb_string(str(p))})" for p in (cert_file(settings), key_file(settings), clients_file(settings))]
    return "\n".join([
        ";; fintrack gate (remote.gate_profile): written by `fintrack remote setup`; don't edit.",
        "(version 1)",
        "(deny default)",
        '(import "bsd.sb")',                                  # what every background process needs (dyld, libSystem)
        # Homebrew's python3.12 re-executes Python.app inside the same framework, so allow running from it (only).
        f"(allow process-exec (subpath {_sb_string(framework)}))",
        "(allow file-read-metadata)",
        "(allow file-read*",
        *[f"    {r}" for r in reads],
        ")",
        f'(allow network-bind (local tcp "*:{settings.remote_port}"))',
        f'(allow network-inbound (local tcp "*:{settings.remote_port}"))',
        f'(allow network-outbound (remote tcp "localhost:{settings.remote_inner_port}"))',
        "",
    ])


def write_gate_profile(settings: Settings) -> Path:
    _write_private(profile_file(settings), gate_profile(settings))
    return profile_file(settings)


def gate_command(settings: Settings) -> list[str]:
    """How launchd starts the gate: under sandbox-exec, Python isolated from the venv and the environment."""
    return ["/usr/bin/sandbox-exec", "-f", str(profile_file(settings)),
            python_executable(), "-I", "-S", str(GATE_SCRIPT),
            "--port", str(settings.remote_port), "--inner-port", str(settings.remote_inner_port),
            "--cert", str(cert_file(settings)), "--key", str(key_file(settings)), "--clients", str(clients_file(settings))]


# --------------------------------------------------------------------------- handing a phone its files

def lan_address() -> str | None:
    """This Mac's address on the home network: the source address for a route out (a UDP 'connect' sends nothing)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))   # TEST-NET-1: never routed anywhere; no packet is sent
            address = probe.getsockname()[0]
        except OSError:
            return None
    return None if address.startswith("127.") else address


def offer_once(files: dict[str, tuple[bytes, str]], on_ready, timeout_s: int = OFFER_S) -> set[str]:
    """Serve `files` ({name: (body, content type)}) on the home network at an unguessable path until each was
    downloaded once or `timeout_s` passes. Calls on_ready(url) with the page's address. Returns the names sent.

    Plain HTTP for a few minutes on your Wi-Fi: the phone's file is password-protected (the password is shown only on
    this Mac), and the authority's fingerprint is shown to compare on the phone.
    """
    address = lan_address()
    if address is None:
        raise RemoteError("This Mac isn't on a network. Connect it to your home Wi-Fi.")
    token = secrets.token_urlsafe(12)
    sent: set[str] = set()
    done = threading.Event()
    index = "".join(f'<p><a href="/{token}/{name}">{name}</a></p>' for name in files).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == f"/{token}/":
                body, kind = b"<!doctype html><meta name=viewport content='width=device-width'>" + index, "text/html"
            else:
                name = self.path.removeprefix(f"/{token}/")
                if not self.path.startswith(f"/{token}/") or name not in files or name in sent:
                    self.send_error(404)
                    return
                body, kind = files[name]
                sent.add(name)
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if kind != "text/html":
                self.send_header("Content-Disposition", f'attachment; filename="{self.path.rsplit("/", 1)[-1]}"')
            self.end_headers()
            self.wfile.write(body)
            if sent == set(files):
                done.set()

        def log_message(self, *args):
            pass

    with http.server.HTTPServer((address, OFFER_PORT), Handler) as httpd:
        httpd.timeout = 1
        on_ready(f"http://{address}:{OFFER_PORT}/{token}/")
        deadline = time.monotonic() + timeout_s
        while not done.is_set() and time.monotonic() < deadline:
            httpd.handle_request()
    return sent


# --------------------------------------------------------------------------- fintrack's phone listener

class ForceHTTPS:
    """The phone listener is plain HTTP on loopback, but phones reached it over HTTPS (through the gate): tell the
    app so, so absolute URLs (static files) are https://, as the page itself is."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope, scheme="https" if scope["type"] == "http" else "wss")
        await self.app(scope, receive, send)


def run_phone_listener(app, settings: Settings, stop: threading.Event) -> None:
    """Serve `app` to the gate on 127.0.0.1:REMOTE_INNER_PORT until `stop`. Runs in its own thread: uvicorn installs no
    signal handlers outside the main thread, so Ctrl-C / launchd still stop the main server, which then sets `stop`."""
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(
        ForceHTTPS(app), host="127.0.0.1", port=settings.remote_inner_port,
        log_config=None, server_header=False, lifespan="off",
    ))
    threading.Thread(target=lambda: (stop.wait(), setattr(server, "should_exit", True)),
                     name="fintrack-phone-stop", daemon=True).start()
    try:
        asyncio.run(server.serve())
    except BaseException as exc:     # uvicorn calls sys.exit(1) when it can't bind; don't take the main server down
        log.warning("Phone listener stopped: %s", exc)


def sandbox_available() -> bool:
    return shutil.which("sandbox-exec", path="/usr/bin") is not None
