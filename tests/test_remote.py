"""Phone access with no app on the phone (remote.py, gate.py, web/remote.py): certificates, the gate (TLS 1.3 + a
phone's client certificate, then a hand-off to 127.0.0.1), its sandbox, passcode and sessions, and the checks on
requests from phones.

The gate tests run gate.serve on 127.0.0.1 with throwaway certificates and a stand-in for fintrack; the sandbox test
runs Python under the gate's real profile. Nothing touches ~/.fintrack or the network beyond loopback.
"""

import asyncio
import dataclasses
import ipaddress
import os
import socket
import ssl
import stat
import subprocess
import sys
import threading
from datetime import timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID

from fintrack import gate, remote
from fintrack.config import ConfigError, load_settings

HOST = "203.0.113.7"          # TEST-NET-3: a documentation address, never anyone's
PORT = 48443
INNER = 8444


@pytest.fixture
def remote_settings(settings):
    return dataclasses.replace(settings, remote_access=True, remote_host=HOST, remote_port=PORT,
                               remote_inner_port=INNER)


@pytest.fixture
def ready(remote_settings):
    """As after `fintrack remote setup --host ...` and `fintrack remote passcode`."""
    remote.setup(remote_settings)
    remote.set_passcode(remote_settings, "correct horse")
    return remote_settings


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def load_p12(p12, password):
    key, cert, extra = pkcs12.load_key_and_certificates(p12, password.encode())
    return key, cert, extra


# ---------------------------------------------------------------- the server certificate

def test_server_certificate_for_your_home_address(ready):
    leaf = x509.load_pem_x509_certificate(remote.cert_file(ready).read_bytes())
    authority = x509.load_pem_x509_certificate(remote.ca_file(ready).read_bytes())
    leaf.verify_directly_issued_by(authority)
    sans = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert sans.get_values_for_type(x509.IPAddress) == [ipaddress.ip_address(HOST)]
    assert ExtendedKeyUsageOID.SERVER_AUTH in leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert leaf.not_valid_after_utc - leaf.not_valid_before_utc <= timedelta(days=825)      # iOS's limit
    # Generic names: scanners record this certificate. No address in the common name (OpenSSL would test it as a DNS
    # name against the constraint and refuse it).
    assert "fintrack" not in leaf.subject.rfc4514_string().lower() + authority.subject.rfc4514_string().lower()
    assert HOST not in leaf.subject.rfc4514_string()
    basic = authority.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert basic.ca is True and basic.path_length == 0
    constraints = authority.extensions.get_extension_for_class(x509.NameConstraints)
    assert constraints.critical
    assert x509.IPAddress(ipaddress.ip_network(f"{HOST}/32")) in constraints.value.permitted_subtrees


def test_server_certificate_for_a_hostname(remote_settings):
    named = dataclasses.replace(remote_settings, remote_host="home.example.net")
    remote.setup(named)
    leaf = x509.load_pem_x509_certificate(remote.cert_file(named).read_bytes())
    assert leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(
        x509.DNSName) == ["home.example.net"]
    assert remote.certificate_expires(named) is not None
    assert remote.certificate_expires(remote_settings) is None      # made for another address: counts as missing


def test_setup_writes_private_files(ready):
    folder = remote.remote_dir(ready)
    assert mode(folder) == 0o700
    for name in ("cert.pem", "key.pem", "ca.crt", "clients.pem", "gate.sb", "passcode", "session.key"):
        assert mode(folder / name) == 0o600, name


def test_setup_needs_your_address(settings):
    with pytest.raises(remote.RemoteError):
        remote.setup(dataclasses.replace(settings, remote_host=""))


# ---------------------------------------------------------------- phones' certificates

def test_phone_certificate_and_nothing_stored(ready):
    p12, password = remote.add_phone(ready, "pixel")
    key, cert, extra = load_p12(p12, password)
    assert ExtendedKeyUsageOID.CLIENT_AUTH in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    trusted = x509.load_pem_x509_certificates(remote.clients_file(ready).read_bytes())
    assert len(trusted) == 1
    cert.verify_directly_issued_by(trusted[0])
    with pytest.raises(ValueError):
        pkcs12.load_key_and_certificates(p12, b"wrong password")
    # Neither the phone's key nor any authority's key is anywhere in the folder: the only private key is the server's.
    phone_key = key.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
    for path in remote.remote_dir(ready).iterdir():
        assert phone_key not in path.read_bytes(), path.name
    assert [p.name for p in remote.remote_dir(ready).iterdir() if b"PRIVATE KEY" in p.read_bytes()] == ["key.pem"]


def test_each_phone_has_its_own_authority(ready):
    remote.add_phone(ready, "pixel")
    remote.add_phone(ready, "iphone")
    assert len(x509.load_pem_x509_certificates(remote.clients_file(ready).read_bytes())) == 2
    remote.add_phone(ready, "pixel")                                     # added again: replaces its own entry
    assert len(x509.load_pem_x509_certificates(remote.clients_file(ready).read_bytes())) == 2
    assert [p["name"] for p in remote.phones(ready)] == ["iphone", "pixel"]
    assert remote.remove_phone(ready, "iphone") is True
    assert len(x509.load_pem_x509_certificates(remote.clients_file(ready).read_bytes())) == 1
    assert remote.remove_phone(ready, "iphone") is False


def load_issuer_key(cert, trusted):
    """The public numbers of whichever trusted authority signed `cert`, or None (it was signed by none of them)."""
    for authority in trusted:
        try:
            cert.verify_directly_issued_by(authority)
            return authority.public_key().public_numbers()
        except Exception:
            continue
    return None


def test_old_certificate_of_a_readded_phone_is_no_longer_trusted(ready):
    p12, password = remote.add_phone(ready, "pixel")
    _, old_cert, _ = load_p12(p12, password)
    remote.add_phone(ready, "pixel")
    trusted = x509.load_pem_x509_certificates(remote.clients_file(ready).read_bytes())
    assert load_issuer_key(old_cert, trusted) is None


def test_phone_needs_setup_first(remote_settings):
    with pytest.raises(remote.RemoteError):
        remote.add_phone(remote_settings, "pixel")


# ---------------------------------------------------------------- the gate's sandbox and command

def test_gate_profile_denies_by_default_and_names_only_its_files(ready):
    profile = remote.gate_profile(ready)
    assert "(deny default)" in profile
    assert f'(local tcp "*:{PORT}")' in profile and f'(remote tcp "localhost:{INNER}")' in profile
    assert "file-write" not in profile
    home = str(ready.home)
    named = [line for line in profile.splitlines() if home in line]
    assert len(named) == 3 and all(("cert.pem" in l or "key.pem" in l or "clients.pem" in l) for l in named)
    assert str(ready.db_path) not in profile and ".env" not in profile


def test_gate_runs_sandboxed_isolated_python(ready):
    command = remote.gate_command(ready)
    assert command[:3] == ["/usr/bin/sandbox-exec", "-f", str(remote.profile_file(ready))]
    assert command[4:6] == ["-I", "-S"] and command[6].endswith("gate.py")
    assert ".fintrack/venv" not in command[3]                               # the real Python, not the venv's


@pytest.mark.skipif(sys.platform != "darwin" or not os.path.exists("/usr/bin/sandbox-exec"), reason="macOS sandbox")
def test_sandbox_blocks_reading_anything_else(ready, tmp_path):
    secret = ready.home / "fintrack-production.db"
    secret.write_text("not for the gate")
    probe = (f"import socket\n"
             f"print(open({str(remote.cert_file(ready))!r}).read()[:10])\n"
             f"try:\n    open({str(secret)!r}).read(); print('READ SECRET')\nexcept PermissionError: print('denied')\n"
             f"try:\n    open('/tmp/fintrack-gate-test', 'w'); print('WROTE')\nexcept PermissionError: print('denied')\n")
    out = subprocess.run(["/usr/bin/sandbox-exec", "-f", str(remote.write_gate_profile(ready)),
                          remote.python_executable(), "-I", "-S", "-c", probe], capture_output=True, text=True, timeout=60)
    assert out.stdout.split() == ["-----BEGIN", "denied", "denied"], out.stdout + out.stderr


# ---------------------------------------------------------------- the gate (no sandbox): TLS + client certificate

def _pem(p12, password, path):
    key, cert, _ = load_p12(p12, password)
    path.write_bytes(cert.public_bytes(Encoding.PEM) + key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    return path


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def running_gate(ready, tmp_path):
    """The gate on 127.0.0.1 in a thread, in front of a stand-in fintrack that counts its connections."""
    p12, password = remote.add_phone(ready, "pixel")
    phone = _pem(p12, password, tmp_path / "phone.pem")
    stranger_settings = dataclasses.replace(ready, home=tmp_path / "stranger")
    remote.setup(stranger_settings)
    stranger = _pem(*remote.add_phone(stranger_settings, "stranger"), tmp_path / "stranger.pem")

    reached = []
    inner = socket.socket()
    inner.bind(("127.0.0.1", 0))
    inner.listen()
    inner_port = inner.getsockname()[1]

    def answer():
        while True:
            try:
                conn, _ = inner.accept()
            except OSError:
                return
            reached.append(1)
            conn.recv(1024)
            conn.sendall(b"HTTP/1.0 200 OK\r\n\r\ninner ok")
            conn.close()

    threading.Thread(target=answer, daemon=True).start()
    port = _free_port()
    context = gate.tls_context(str(remote.cert_file(ready)), str(remote.key_file(ready)), str(remote.clients_file(ready)))
    loop = asyncio.new_event_loop()
    started = threading.Event()

    def run():
        asyncio.set_event_loop(loop)
        ready_event = asyncio.Event()
        task = loop.create_task(gate.serve(port, inner_port, context, host="127.0.0.1", ready=ready_event))
        loop.run_until_complete(ready_event.wait())
        started.set()
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            pass

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert started.wait(10)
    yield {"port": port, "phone": phone, "stranger": stranger, "reached": reached, "ca": remote.ca_file(ready)}
    for task in asyncio.all_tasks(loop):
        loop.call_soon_threadsafe(task.cancel)
    thread.join(5)
    inner.close()


def _connect(gate_info, certfile=None, tls12=False):
    context = ssl.create_default_context(cafile=str(gate_info["ca"]))
    if tls12:
        context.maximum_version = ssl.TLSVersion.TLSv1_2
    if certfile:
        context.load_cert_chain(str(certfile))
    try:
        with socket.create_connection(("127.0.0.1", gate_info["port"]), 5) as raw, \
                context.wrap_socket(raw, server_hostname=HOST) as tls:      # full check of the address, as browsers do
            tls.sendall(b"GET / HTTP/1.0\r\n\r\n")
            return tls.recv(4096)
    except (ssl.SSLError, ConnectionError, OSError):
        return None


def test_gate_lets_your_phone_through(running_gate):
    assert _connect(running_gate, running_gate["phone"]).endswith(b"inner ok")
    assert running_gate["reached"] == [1]


@pytest.mark.parametrize("who", ["nobody", "stranger", "tls12"])
def test_gate_refuses_everyone_else_before_fintrack(running_gate, who):
    certfile = {"nobody": None, "stranger": running_gate["stranger"], "tls12": running_gate["phone"]}[who]
    assert not _connect(running_gate, certfile, tls12=(who == "tls12"))       # nothing back
    assert running_gate["reached"] == []                                         # fintrack never reached


# ---------------------------------------------------------------- passcode, sessions, throttle, settings

def test_passcode_and_sessions(ready):
    assert remote.check_passcode(ready, "correct horse")
    assert not remote.check_passcode(ready, "wrong horse")
    token = remote.new_session(ready, now=1_000_000)
    assert remote.valid_session(ready, token, now=1_000_000 + 86400)
    assert not remote.valid_session(ready, token, now=1_000_000 + 31 * 86400)       # 30 days
    assert not remote.valid_session(ready, token.replace(".", ".0"), now=1_000_000)  # tampered
    remote.set_passcode(ready, "a new passcode")                                     # signs every phone out
    assert not remote.valid_session(ready, token, now=1_000_000 + 86400)
    with pytest.raises(remote.RemoteError):
        remote.set_passcode(ready, "123456789")                                      # 10 at least (2026-11-11)
    remote.set_passcode(ready, "1234567890")


def test_throttle_locks_after_five_wrong_passcodes():
    throttle = remote.Throttle()
    for _ in range(remote.MAX_FAILURES):
        assert not throttle.locked(now=100)
        throttle.fail(now=100)
    assert throttle.locked(now=100)
    assert not throttle.locked(now=100 + remote.FAILURE_WINDOW_S)                    # ages out


@pytest.mark.parametrize("value", ["127.0.0.1", "https://x.example", "a b", "x.example:443"])
def test_bad_remote_host_refused(home, monkeypatch, value):
    monkeypatch.setenv("REMOTE_HOST", value)
    with pytest.raises(ConfigError):
        load_settings(require_plaid=False)


@pytest.mark.parametrize("host, port, origin", [
    (HOST, 48443, f"https://{HOST}:48443"),
    ("home.example.net", 443, "https://home.example.net"),
    ("2001:db8::7", 48443, "https://[2001:db8::7]:48443"),
])
def test_remote_origin(settings, host, port, origin):
    assert dataclasses.replace(settings, remote_host=host, remote_port=port).remote_origin == origin


# ---------------------------------------------------------------- requests from phones (web/remote.py)

def _client(settings, cipher, gateway, phone):
    """A phone (through the gate: the phone listener, 127.0.0.1:INNER, as https) or this Mac's own browser."""
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    app = create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher)
    if phone:
        return TestClient(remote.ForceHTTPS(app), base_url=f"https://127.0.0.1:{INNER}",
                          headers={"host": f"{HOST}:{PORT}"}, follow_redirects=False)
    return TestClient(app, base_url="https://localhost:8443", follow_redirects=False)


@pytest.fixture
def phone(ready, conn, cipher, gateway):
    return _client(ready, cipher, gateway, phone=True)


PHONE_ORIGIN = f"https://{HOST}:{PORT}"


def sign_in(phone, passcode="correct horse"):
    return phone.post("/remote-login", data={"passcode": passcode, "next": "/"}, headers={"origin": PHONE_ORIGIN})


def test_phone_must_enter_passcode_then_is_remembered(phone):
    response = phone.get("/transactions?q=coffee")
    assert response.status_code == 303 and response.headers["location"] == "/remote-login?next=/transactions?q=coffee"
    page = phone.get("/remote-login")
    assert page.status_code == 200 and f'href="https://{HOST}:{PORT}/static/app.css"' in page.text   # https links
    assert phone.get("/static/app.css").status_code == 200

    assert sign_in(phone, "wrong").status_code == 401
    response = sign_in(phone)
    assert response.status_code == 303
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie and "max-age=2592000" in cookie
    assert phone.get("/").status_code == 200


def test_htmx_after_session_ends_is_sent_to_passcode(phone):
    response = phone.get("/", headers={"hx-request": "true"})
    assert response.status_code == 401 and response.headers["hx-redirect"] == "/remote-login"


def test_wrong_passcodes_lock_the_page(phone):
    for _ in range(remote.MAX_FAILURES):
        assert sign_in(phone, "wrong").status_code == 401
    assert sign_in(phone).status_code == 429                       # even the right one, until the window passes


def test_bank_connections_stay_on_the_mac(phone):
    sign_in(phone)
    for path in ("/connect", "/oauth-return"):
        assert phone.get(path).status_code == 403
    for path in ("/link/token/create", "/items/1/remove", "/items/1/link-token"):
        assert phone.post(path, headers={"origin": PHONE_ORIGIN}).status_code == 403


def test_imports_stay_on_the_mac(phone):
    """The Import page shows phones a note; its actions are refused (user's decision 2026-11-13)."""
    sign_in(phone)
    page = phone.get("/import")
    assert page.status_code == 200 and "Import files on the Mac." in page.text and 'name="file"' not in page.text
    for path in ("/import/preview", "/import/apply", "/import/1/undo"):
        assert phone.post(path, headers={"origin": PHONE_ORIGIN}).status_code == 403


def test_phone_forms_must_come_from_the_phone_address(phone):
    sign_in(phone)
    response = phone.post("/sync", headers={"origin": "https://localhost:8443"})
    assert response.status_code == 403 and "Cross-origin" in response.text


def test_nothing_changes_on_the_mac(ready, conn, cipher, gateway):
    local = _client(ready, cipher, gateway, phone=False)
    assert local.get("/").status_code == 200                       # no passcode on the Mac
    assert local.get("/remote-login").status_code == 303           # nothing to sign in to
    assert local.get("/connect").status_code == 200                # bank connections work here


def test_phone_address_refused_when_phone_access_is_off(settings, conn, cipher, gateway):
    off = _client(dataclasses.replace(settings, remote_host=HOST, remote_inner_port=INNER), cipher, gateway, phone=True)
    assert off.get("/").status_code == 400                         # not an allowed host

