"""The gate: the only fintrack process that faces the internet (phone access; user's decision 2026-11-05).

It accepts TLS 1.3 connections on one public port, requires a client certificate issued to one of your phones during
the handshake, and only then passes the connection, decrypted, to fintrack's phone listener on 127.0.0.1. Anyone
without a phone's certificate is refused before any of fintrack's own code is reached.

It runs as its own process under macOS's sandbox (`sandbox-exec`, profile written by remote.write_gate_profile), allowed
to read Python and its certificate files, listen on its port and connect to 127.0.0.1:<inner port>, and nothing else:
not your files, fintrack's database or the Keychain. If the TLS code were ever exploited, the attacker would be inside
that box. So this file uses the standard library only (it runs as `python -I -S gate.py`, no venv, no fintrack import).

    python -I -S gate.py --port 48443 --inner-port 8444 --cert cert.pem --key key.pem --clients clients.pem
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import ssl
import sys
import time

log = logging.getLogger("fintrack.gate")

HANDSHAKE_TIMEOUT_S = 10
IDLE_TIMEOUT_S = 300          # a connection with no traffic either way for this long is closed
MAX_CONNECTIONS = 64          # phones open a handful; more is someone else
REFUSAL_LOG_EVERY_S = 60      # refused handshakes are logged at most once a minute, with a count


def tls_context(cert: str, key: str, clients: str) -> ssl.SSLContext:
    """TLS 1.3 only; a client certificate from one of your phones' authorities (clients.pem) is required."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_cert_chain(cert, key)
    context.load_verify_locations(cafile=clients)
    return context


class Refusals:
    """Counts refused handshakes and logs them sparingly (scanners try often)."""

    def __init__(self) -> None:
        self.count = 0
        self.last_logged = 0.0

    def add(self, peer: str, reason: str) -> None:
        self.count += 1
        now = time.monotonic()
        if now - self.last_logged >= REFUSAL_LOG_EVERY_S:
            log.info("refused %d connection(s); latest from %s: %s", self.count, peer, reason)
            self.count, self.last_logged = 0, now


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await asyncio.wait_for(reader.read(65536), IDLE_TIMEOUT_S)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (asyncio.TimeoutError, ConnectionError, ssl.SSLError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def serve(port: int, inner_port: int, context: ssl.SSLContext, host: str = "0.0.0.0",
                ready: asyncio.Event | None = None) -> None:
    """Listen until cancelled. `host` is all interfaces (the router forwards the public port here); tests use 127.0.0.1."""
    slots = asyncio.Semaphore(MAX_CONNECTIONS)
    refusals = Refusals()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # Reached only after a completed handshake with a valid client certificate (CERT_REQUIRED).
        if slots.locked():
            writer.close()
            return
        async with slots:
            try:
                inner_reader, inner_writer = await asyncio.open_connection("127.0.0.1", inner_port)
            except OSError:
                writer.close()     # fintrack isn't running
                return
            await asyncio.gather(_pipe(reader, inner_writer), _pipe(inner_reader, writer))

    loop = asyncio.get_running_loop()

    def on_error(loop, context) -> None:
        # Failed handshakes (no certificate, wrong one, old TLS, scanners) surface here; log them quietly.
        exc = context.get("exception")
        transport = context.get("transport")
        peer = transport.get_extra_info("peername") if transport else None
        if isinstance(exc, (ssl.SSLError, ConnectionError, asyncio.TimeoutError, OSError)):
            refusals.add(str(peer[0]) if peer else "?", type(exc).__name__ + ": " + str(exc)[:80])
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(on_error)
    server = await asyncio.start_server(handle, host, port, ssl=context, ssl_handshake_timeout=HANDSHAKE_TIMEOUT_S,
                                        reuse_address=True)
    log.info("gate listening on %s:%s -> 127.0.0.1:%s", host, port, inner_port)
    if ready:
        ready.set()
    async with server:
        await server.serve_forever()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="fintrack-gate")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--inner-port", type=int, required=True)
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--clients", required=True)
    parser.add_argument("--listen", default="0.0.0.0", help="address to listen on (default all: the router forwards here)")
    args = parser.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    context = tls_context(args.cert, args.key, args.clients)
    try:
        asyncio.run(serve(args.port, args.inner_port, context, host=args.listen))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
