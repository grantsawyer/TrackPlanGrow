"""Command-line entry point: `fintrack <command>`.

    fintrack init      create ~/.fintrack, the .env file, the Keychain key, and the database
    fintrack certs     create the localhost HTTPS certificate with mkcert
    fintrack serve     start the web app at https://localhost:8443
    fintrack migrate   apply database migrations (backs up first)
    fintrack backup    copy the database to a dated file in ~/.fintrack/backups
    fintrack items     show Plaid Items and the Production Item budget
    fintrack sync      sync transactions and balances for all connections
    fintrack schedule  install | uninstall | status of the scheduled sync (launchd)
    fintrack autostart install | uninstall | status | restart of the web server at login (launchd)
    fintrack remote    phone access, no app on the phone: setup, phone, remove, passcode, enable, disable, status
    fintrack preflight check the Production setup before connecting real banks (creates no Item)
    fintrack demo      fill the SANDBOX with made-up accounts and ~21 months of activity (net worth ~$500k)
    fintrack import    bank files of any layout into an account (preview, --apply, --history, --undo)
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from fintrack import account_names, budgets, db
from fintrack.backup import backup_database
from fintrack.config import HOST, ConfigError, Settings, check_home_outside_repo, load_settings, resolve_home
from fintrack.crypto import ensure_key

ENV_TEMPLATE = Path(__file__).resolve().parents[1] / ".env.example"


def cmd_init(_args) -> None:
    home = resolve_home()
    check_home_outside_repo(home)

    # Data folder and subfolders: readable by your user account only.
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    home.chmod(0o700)
    for sub in ("certs", "backups", "logs"):
        (home / sub).mkdir(mode=0o700, exist_ok=True)
    print(f"Data folder: {home}")

    env_file = home / ".env"
    if env_file.exists():
        print(f".env already exists: {env_file}")
    else:
        shutil.copyfile(ENV_TEMPLATE, env_file)
        print(f"Created {env_file}. Fill in your Plaid keys.")
    env_file.chmod(0o600)

    if ensure_key(home):
        print("Created token encryption key in the macOS Keychain.")
    else:
        print("Token encryption key already in the Keychain.")

    settings = load_settings(require_plaid=False)
    _migrate(settings)

    print("\nNext: fill in the .env file, run `fintrack certs`, then `fintrack serve`.")


def cmd_certs(_args) -> None:
    settings = load_settings(require_plaid=False)
    # Fall back to Homebrew's Apple Silicon location in case it isn't on PATH
    # (e.g. when run from launchd or a shell that hasn't loaded ~/.zprofile).
    mkcert = shutil.which("mkcert") or shutil.which("mkcert", path="/opt/homebrew/bin")
    if not mkcert:
        sys.exit("mkcert not found. Install it with: brew install mkcert")
    settings.certs_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    subprocess.run(
        [
            mkcert,
            "-cert-file", str(settings.cert_file),
            "-key-file", str(settings.key_file),
            "localhost", "127.0.0.1", "::1",
        ],
        check=True,
    )
    settings.key_file.chmod(0o600)
    print(f"\nCertificate written to {settings.certs_dir}")
    print("If you haven't yet, run `mkcert -install` once so your browser trusts it.")


def _migrate(settings: Settings, quiet: bool = False) -> None:
    """Apply pending migrations, backing up an existing database first."""
    conn = db.connect(settings.db_path)
    try:
        if not db.pending_migrations(conn):
            if not quiet:
                print("Database is up to date.")
            return
        # Back up an existing database before changing its schema.
        if conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]:
            print(f"Backed up database to {backup_database(settings.db_path, settings.backups_dir)}")
        applied = db.migrate(conn)
        for name in applied:
            print(f"Applied migration {name}")
        # Shorten existing Plaid account names now (0011) instead of at the next sync.
        account_names.refresh_auto_names(conn)
        # Budget sections (0015): Insurance is a fixed line by default, which needs Services split.
        if "0015_budget_lines.sql" in applied and budgets.add_insurance_line(conn):
            print("Budgets: split Services into subcategories and added Insurance to Fixed expenses.")
    finally:
        conn.close()


def cmd_migrate(_args) -> None:
    _migrate(load_settings(require_plaid=False))


def cmd_backup(_args) -> None:
    settings = load_settings(require_plaid=False)
    print(f"Backed up to {backup_database(settings.db_path, settings.backups_dir)}")


def cmd_items(_args) -> None:
    """Show the Production Item budget and every Item in this environment's database."""
    from fintrack.item_budget import production_item_budget

    settings = load_settings(require_plaid=False)
    _migrate(settings, quiet=True)
    conn = db.connect(settings.db_path)
    try:
        budget = production_item_budget(conn, settings.production_item_limit)
        print(f"Environment: {settings.plaid_env}  (database: {settings.db_path.name})")
        if settings.is_production:
            print(f"Production Items: {budget.used} of {budget.limit} used, {budget.remaining} remaining "
                  "(removed Items still count)")
        else:
            print("Sandbox Items don't count toward the Production limit.")
        rows = conn.execute(
            "SELECT id, institution_name, status, created_at, removed_at FROM items ORDER BY id"
        ).fetchall()
        if not rows:
            print("No Items.")
        for row in rows:
            removed = f"  removed {row['removed_at']}" if row["removed_at"] else ""
            print(f"  #{row['id']:<3} {row['institution_name'] or 'Unknown':<30} {row['status']:<15}"
                  f" created {row['created_at']}{removed}")
    finally:
        conn.close()


def cmd_sync(_args) -> None:
    """Sync all Items. Exit code 1 if any Item failed, so schedulers can notice."""
    from fintrack.crypto import TokenCipher
    from fintrack.demo import make_gateway
    from fintrack.logging_setup import configure_logging
    from fintrack.sync import SyncInProgress, sync_all

    settings = load_settings()
    configure_logging(settings.logs_dir, secrets=settings.secrets)
    _migrate(settings, quiet=True)
    conn = db.connect(settings.db_path)
    try:
        plaid_gateway = make_gateway(settings)
        summary = sync_all(conn, plaid_gateway, TokenCipher(settings.home), settings.home,
                           settings.sync_investment_transactions)
        # Alpaca: daily price history (one call), then today's benchmark prices (performance
        # cards) and the values of manual accounts with positions (without keys: Plaid's / your
        # last prices). Alpaca failures only log a warning (quotes.py) and never fail the sync.
        from fintrack import live_values, manual_positions, performance, price_history, prices
        from fintrack.quotes import QuoteGateway
        gateway = (QuoteGateway(settings.alpaca_key_id, settings.alpaca_secret_key)
                   if settings.quotes_enabled else None)
        if gateway:
            price_history.backfill(conn, gateway)
            from fintrack import watchlist
            watchlist.refresh_assets(conn, gateway)     # the watchlist's company search, about weekly (2026-11-04)
        live = (gateway.quotes(performance.benchmark_symbols() + prices.held_tickers(conn)
                               + manual_positions.position_tickers(conn))
                if gateway else {})
        performance.record_benchmarks(conn, live)
        live_values.revalue_all(conn, live)
        # Merchant logos the new transactions point to, and each institution's logo for the Balances page
        # (logos.py; never fails the sync).
        from fintrack import logos
        logos.fetch_missing(conn, settings.home)
        logos.fetch_institution_logos(conn, settings.home, plaid_gateway)
        logos.find_manual_institution_logos(conn, settings.home, plaid_gateway)
    except SyncInProgress as exc:
        sys.exit(str(exc))
    finally:
        conn.close()
    for r in summary.results:
        detail = f"error {r.error}" if r.error else (
            f"+{r.added} ~{r.modified} -{r.removed}" + (f" ({r.note})" if r.note else "")
            + (f" [{r.transactions_status}]" if r.transactions_status else "")
        )
        print(f"  {r.institution}: {detail}")
    if summary.errors:
        sys.exit(1)


def cmd_schedule(args) -> None:
    from fintrack import schedule

    settings = load_settings()  # require keys: a scheduled sync without them would only fail
    if args.action == "install":
        try:
            times = schedule.parse_times(args.times)
        except ValueError as exc:
            sys.exit(str(exc))
        schedule.install(settings, times)
        print(schedule.status())
    elif args.action == "uninstall":
        print("Removed scheduled sync." if schedule.uninstall() else "No scheduled sync was installed.")
    else:
        print(schedule.status())


def cmd_preflight(_args) -> None:
    """Check the PRODUCTION setup (even while PLAID_ENV is sandbox). Creates no Item."""
    import os

    from fintrack import preflight
    from fintrack.plaid_client import PlaidGateway

    previous = os.environ.get("PLAID_ENV")
    os.environ["PLAID_ENV"] = "production"
    try:
        settings = load_settings(require_plaid=False)
    finally:
        if previous is None:
            os.environ.pop("PLAID_ENV", None)
        else:
            os.environ["PLAID_ENV"] = previous

    print("Production pre-flight (no Item is created)\n")
    checks = preflight.run(settings, PlaidGateway)
    for c in checks:
        print(f"  {'✓' if c.ok else '✕'} {c.name}: {c.detail}")
        if c.fix:
            print(f"      → {c.fix}")
    if all(c.ok for c in checks):
        print("\nReady. To switch, set PLAID_ENV=production in .env and restart `fintrack serve`.")
    else:
        sys.exit("\nNot ready yet. Fix the items marked ✕ and run `fintrack preflight` again.")


def cmd_locations(args) -> None:
    """Where transactions happened: --backfill fills it in for existing transactions (once; read-only at Plaid)."""
    from fintrack import sync
    from fintrack.crypto import TokenCipher
    from fintrack.demo import make_gateway
    from fintrack.logging_setup import configure_logging

    if not args.backfill:
        sys.exit("Nothing to do: use --backfill (and --dry-run to only count).")
    settings = load_settings()
    configure_logging(settings.logs_dir, secrets=settings.secrets)
    _migrate(settings, quiet=True)
    conn = db.connect(settings.db_path)
    try:
        with sync.sync_lock(settings.home):            # never alongside a sync
            report = sync.backfill_locations(conn, make_gateway(settings), TokenCipher(settings.home),
                                             dry_run=args.dry_run, item_id=args.item)
        for r in report:
            print(f"  Item {r['item']}: {r['fetched']} transactions from Plaid, {r['with_location']} with a location"
                  + (f", {r['updated']} updated" if not args.dry_run else "")
                  + f"; {r['with_owner']} with a card holder (account_owner)"
                  + (f", {r['owners_set']} set" if not args.dry_run else " (dry run: nothing written)")
                  + (f", error {r['error']}" if r["error"] else ""))
    except sync.SyncInProgress as exc:
        sys.exit(str(exc))
    finally:
        conn.close()


def _n(count: int, word: str, plural: str | None = None) -> str:
    return f"{count} {word if count == 1 else plural or word + 's'}"


def _print_plan(plan, money) -> None:
    """The preview of one file: how it was read, the overlap question, and what would be added."""
    from fintrack.imports.layout import ROLES

    d, a = plan.detection, plan.account
    print(f"{plan.file_name} -> {a.name}" + (f" (····{a.mask})" if a.mask else ""))
    where = "no header row" if d.header is None else f"header on row {d.data_start}"
    names = {i: (d.header[i] if d.header and i < len(d.header) and d.header[i] else f"column {i + 1}")
             for i in range(d.width)}
    roles = ", ".join(f"{role} = {names[i]!r}" for role in ROLES if (i := d.layout.columns.get(role)) is not None)
    print(f"  Layout ({'saved' if d.source == 'profile' else 'detected'}; {where}): {roles}")
    for note in d.notes:
        print(f"  {note}")
    if plan.skipped:
        shown = "; ".join(f"row {line}: {why}" for line, why in plan.skipped[:5])
        print(f"  {_n(len(plan.skipped), 'row')} not read ({shown}{'; ...' if len(plan.skipped) > 5 else ''})")
    if plan.kind != "transactions":
        _print_investment_plan(plan, money)
    o = plan.overlap
    if o:
        have = " and ".join(f"{start} on" if end is None else start if start == end else f"{start} to {end}"
                            for start, end in o.have)
        print(f"  Overlap: the file has {o.file_from} to {o.file_to}; the account already has transactions {have}.")
        print(f"    --overlap outside  {_n(o.outside, 'row')} (only dates the account doesn't have, no likely duplicates)")
        print(f"    --overlap skip     {_n(o.skip, 'row')} ({_n(o.duplicates, 'likely duplicate')} left out)")
        print(f"    --overlap review   {_n(o.skip, 'row')} + the likely duplicates you name with --take ROW,ROW")
        dupes = [p for p in plan.rows if p.duplicate_of]
        for p in dupes[:20]:
            e = p.duplicate_of
            print(f"      row {p.row.line}: {p.row.posted} {money(p.row.amount_cents)} {p.row.description[:40]}"
                  f"  ~ {e.date} {e.name[:40]}" + ("  (taken)" if p.take else ""))
        if len(dupes) > 20:
            print(f"      ... and {len(dupes) - 20} more")
        if plan.choice:
            print(f"    Chosen: {plan.choice}")
    new = plan.new
    if plan.kind != "transactions":
        new = []
    elif "overlap" in plan.needs:
        print("  What's added depends on your overlap choice.")
    else:
        print(f"  {len(new)} to add" + (f" ({new[0].row.posted} to {max(p.row.posted for p in new)})" if new else "")
              + f", {plan.known_count} with the merchant's category from Plaid")
    if new:
        out = sum(p.row.amount_cents for p in new if p.row.amount_cents > 0)
        came = -sum(p.row.amount_cents for p in new if p.row.amount_cents < 0)
        print(f"    money out {money(out)}, money in {money(came)}; first rows:")
        for p in new[:3]:
            print(f"      {p.row.posted} {money(p.row.amount_cents):>12}  {p.row.description[:50]}")
    if plan.count("already"):
        print(f"  {plan.count('already')} imported before"
              + (f" ({len(plan.cards_missing)} without their card number: filled in on import)" if plan.cards_missing else ""))
    if plan.cards:
        print("  Cards: " + ", ".join(f"····{c} ({_n(n, 'row')}{', primary' if c == plan.primary_card else ''})"
                                      for c, n in plan.cards.most_common())
              + (f"; the account's number becomes ····{plan.primary_card}" if plan.primary_change and plan.account.is_manual
                 else ""))
    if plan.unmapped:
        print("  Bank categories with no match (Uncategorized): "
              + ", ".join(f"{name} ({n})" for name, n in plan.unmapped.most_common()))
    asks = {"date_order": "month first or day first? --dates mdy or --dates dmy",
            "sign": "check the amounts above; if money out shows as money in, use --money-out negative "
                    "(else --money-out positive)",
            "overlap": "choose --overlap outside, skip or review",
            "primary_card": "which card is the account's own? --primary-card LAST4 (the others are authorized users')",
            "as_of": "the statement's date isn't in the file: --as-of YYYY-MM-DD",
            "problems": "see the problem above"}
    for need in plan.needs:
        print(f"  To answer before --apply: {asks[need]}")


def _print_investment_plan(plan, money) -> None:
    """An investment file's preview: its actions (activity) or statement date (holdings), problems, what it adds."""
    from fintrack.imports import investments

    if plan.kind == "activity":
        print("  Actions: " + "; ".join(f"{v[1][:30]!r} = {investments.ACTION_KINDS[v[0]]} ({_n(v[2], 'row')})"
                                        for v in plan.action_keys.values()))
        for p in plan.new[:3]:
            e = p.effect
            print(f"      {p.row.posted}  {investments.ACTION_KINDS[p.kind]:<20} {p.row.symbol:<8}"
                  + (f" {e.shares:+} shares" if e.shares is not None else "") + (f" cash {money(e.cash)}" if e.cash else ""))
    for symbol, v in sorted(plan.holding_keys.items()):
        t = v["target"]
        print(f"  {symbol} -> " + (f"{t.ticker or t.name}" + (" (by dollars)" if t.by_dollars else "")
                                   if t.line_id is not None else "a new line")
              + "  (change with --holding SYMBOL=TICKER or SYMBOL=new)")
    if plan.kind == "positions":
        print(f"  Holdings as of {plan.as_of or '?'}: "
              + ", ".join(f"{p.line or 'Cash'} {p.row.quantity if p.row.quantity is not None else money(p.row.value_cents)}"
                          for p in plan.new)
              + (f"; set to 0: {', '.join(plan.zeroed)}" if plan.zeroed else ""))
    if plan.unpaired:
        print(f"  {_n(plan.unpaired, 'reinvestment')} without a dividend in the file: "
              + ("a dividend is added for each (--no-added-dividends: don't)" if plan.add_dividends
                 else "none added (--added-dividends: add them)"))
    for problem in plan.problems:
        print(f"  Problem: {problem}")
    print(f"  {_n(len(plan.new), 'row')} to add" + (f", {plan.count('already')} imported before" if plan.count("already") else ""))


def _parse_columns(text: str | None) -> dict[str, int] | None:
    """'date=1,description=3,amount=5' (columns counted from 1) -> {'date': 0, ...}."""
    from fintrack.imports.layout import ROLES

    if not text:
        return None
    found = {}
    for part in text.split(","):
        role, _, number = part.partition("=")
        role = role.strip().lower()
        if role not in ROLES or not number.strip().isdigit() or int(number) < 1:
            sys.exit(f"--columns: {part!r} isn't ROLE=NUMBER (roles: {', '.join(ROLES)}).")
        found[role] = int(number) - 1
    return found


def cmd_convert_positions(args) -> None:
    """Manual investment accounts' typed contributions / withdrawals / fees into activity (convert_positions.py,
    docs/parity-plan.md phase 4): checked on a copy first; --apply converts the real database when the copy matches."""
    from fintrack import convert_positions, sync

    settings = load_settings()
    _migrate(settings, quiet=True)
    scratch = settings.home / "convert-check.db"
    try:
        count, differences = convert_positions.check_on_copy(settings.db_path, scratch)
    except ValueError as exc:
        sys.exit(f"Not converted: {exc}")
    print(f"{_n(count, 'entry', 'entries')} to convert, checked on a copy of the database:")
    if differences:
        for line in differences:
            print(f"  {line}")
        sys.exit("Not converted: the copy doesn't match (see above).")
    print("  every manual investment account's values, snapshots, balance and holdings, and every month's Savings"
          " figures, match to the cent.")
    if not args.apply:
        print("Nothing converted yet. Add --apply to convert.")
        return
    conn = db.connect(settings.db_path)
    try:
        with sync.sync_lock(settings.home):
            print(f"Backed up database to {backup_database(settings.db_path, settings.backups_dir)}")
            before = convert_positions.snapshot(conn)
            converted = convert_positions.convert(conn)
            after = convert_positions.snapshot(conn)
        differences = convert_positions.compare(before, after)
        if differences:
            for line in differences:
                print(f"  {line}")
            sys.exit("Converted, but something differs: restore the backup above (fintrack backup's folder).")
        print(f"Converted {_n(converted, 'entry', 'entries')}; everything still matches.")
    except sync.SyncInProgress as exc:
        sys.exit(str(exc))
    finally:
        conn.close()


def cmd_import(args) -> None:
    """Bank files of any layout into an account (fintrack/imports/): shows what each would add; --apply imports it as
    one batch after a backup. --history lists past imports; --undo BATCH removes one."""
    from fintrack import sync
    from fintrack.imports import engine
    from fintrack.money import format_cents

    settings = load_settings()
    _migrate(settings, quiet=True)
    conn = db.connect(settings.db_path)
    try:
        if args.history:
            rows = engine.batches(conn, args.account)
            for b in rows:
                print(f"  {b['id']:>4}  {b['created_at']}  {b['account_name']}"
                      + (f" (····{b['account_mask']})" if b['account_mask'] else "")
                      + f"  {b['file_name']}  {b['rows_added']} rows {b['date_from']} to {b['date_to']}"
                      + (f"  undone {b['undone_at']}" if b['undone_at'] else ""))
            print(f"{_n(len(rows), 'import')}." if rows else "No imports yet.")
            return
        if args.undo is not None:
            batch = conn.execute("SELECT rows_added, file_name, undone_at FROM import_batches WHERE id = ?",
                                 (args.undo,)).fetchone()
            if batch is None or batch[2]:
                sys.exit(f"No import {args.undo} to undo.")
            print(f"Import {args.undo}: {batch[1]}, {batch[0]} transactions (your edits to them go too).")
            if not args.apply:
                print("Preview only: nothing removed. Add --apply to undo it.")
                return
            with sync.sync_lock(settings.home):
                print(f"Backed up database to {backup_database(settings.db_path, settings.backups_dir)}")
                print(f"Removed {engine.undo(conn, args.undo)} transactions.")
            return
        if not args.files or args.account is None:
            sys.exit("Name the file(s) and --account ID (or use --history / --undo).")
        sign = {"positive": 1, "negative": -1}.get(args.money_out)
        take = {int(n) for n in args.take.split(",") if n.strip().isdigit()} if args.take else set()
        backed_up = False
        for name in args.files:
            path = Path(name)
            plan = engine.prepare(conn, args.account, path.read_bytes(), path.name, columns=_parse_columns(args.columns),
                                  date_order=args.dates, sign=sign, overlap=args.overlap, take=take, before=args.before,
                                  primary_card=args.primary_card, kind=args.kind, as_of=args.as_of,
                                  holdings=dict(h.split("=", 1) for h in args.holding if "=" in h) or None,
                                  add_dividends=args.added_dividends)
            _print_plan(plan, format_cents)
            if not args.apply:
                print("  Preview only: nothing written. Add --apply to import.")
                continue
            if plan.needs:
                sys.exit("  Not imported: answer the question(s) above first.")
            if not plan.new and not plan.cards_missing and not plan.primary_change and not plan.zeroed:
                print("  Nothing to add.")
                continue
            with sync.sync_lock(settings.home):        # never alongside a sync
                if not backed_up:
                    print(f"  Backed up database to {backup_database(settings.db_path, settings.backups_dir)}")
                    backed_up = True
                batch, added = engine.apply(conn, plan)
            print(f"  Added {added} transactions" + (f" (import {batch}; undo with --undo {batch})" if batch else "")
                  + (f"; card number filled in on {len(plan.cards_missing)} imported before." if plan.cards_missing else "."))
    except (engine.ImportFileError, OSError) as exc:
        sys.exit(str(exc))
    except sync.SyncInProgress as exc:
        sys.exit(str(exc))
    finally:
        conn.close()


def cmd_descriptions(args) -> None:
    """Raw bank text (destination rules): --backfill fills it in for existing transactions (once; read-only at Plaid)."""
    from fintrack import sync
    from fintrack.crypto import TokenCipher
    from fintrack.demo import make_gateway
    from fintrack.logging_setup import configure_logging

    if not args.backfill:
        sys.exit("Nothing to do: use --backfill (and --dry-run to only count).")
    settings = load_settings()
    configure_logging(settings.logs_dir, secrets=settings.secrets)
    _migrate(settings, quiet=True)
    conn = db.connect(settings.db_path)
    try:
        with sync.sync_lock(settings.home):            # never alongside a sync
            report = sync.backfill_original_descriptions(conn, make_gateway(settings), TokenCipher(settings.home),
                                                         dry_run=args.dry_run)
        for r in report:
            print(f"  Item {r['item']}: {r['fetched']} transactions from Plaid, {r['with_text']} with raw text"
                  + (f", {r['updated']} updated" if not args.dry_run else " (dry run: nothing written)")
                  + (f", error {r['error']}" if r["error"] else ""))
    except sync.SyncInProgress as exc:
        sys.exit(str(exc))
    finally:
        conn.close()


def cmd_logos(args) -> None:
    """Merchant logos: --backfill fills in logo data for existing transactions (once), then downloads."""
    from fintrack import logos
    from fintrack.crypto import TokenCipher
    from fintrack.demo import make_gateway
    from fintrack.logging_setup import configure_logging
    from fintrack.sync import SyncInProgress, sync_lock

    settings = load_settings()
    configure_logging(settings.logs_dir, secrets=settings.secrets)
    _migrate(settings, quiet=True)
    conn = db.connect(settings.db_path)
    try:
        if args.backfill:
            with sync_lock(settings.home):             # never alongside a sync
                report = logos.backfill(conn, make_gateway(settings), TokenCipher(settings.home),
                                        settings.home, dry_run=args.dry_run)
            for r in report:
                print(f"  Item {r['item']}: {r['fetched']} transactions from Plaid, {r['with_logo']} with a logo,"
                      f" {r['with_merchant_id']} with a merchant id, {r['with_bank_logo']} with a bank's logo"
                      + (f", {r['updated']} updated" if not args.dry_run else " (dry run: nothing written)")
                      + (f", error {r['error']}" if r["error"] else ""))
        saved, failed = (0, 0) if args.dry_run else logos.fetch_missing(conn, settings.home)
        if not args.dry_run:
            have = conn.execute("SELECT COUNT(*) FROM logo_files WHERE status = 'ok'").fetchone()[0]
            print(f"  Logos: {saved} downloaded now, {failed} unavailable, {have} cached in total.")
    except SyncInProgress as exc:
        sys.exit(str(exc))
    finally:
        conn.close()


def cmd_autostart(args) -> None:
    """The web server at login (launchd job com.fintrack.serve), for Chrome's installed app (README)."""
    import socket

    from fintrack import schedule

    settings = load_settings(require_plaid=False)  # as `fintrack serve`: it starts without Plaid keys
    if args.action == "install":
        if not (settings.cert_file.exists() and settings.key_file.exists()):
            sys.exit("No HTTPS certificate found. Run `fintrack certs` first.")
        # A server already on the port (a `fintrack serve` in a terminal): the job retries every 30 s until it's free.
        with socket.socket() as probe:
            busy = probe.connect_ex((HOST, settings.port)) == 0
        if busy and not schedule.plist_path(schedule.SERVE_LABEL).exists():
            print(f"Port {settings.port} is in use (a `fintrack serve` in a terminal?). Stop it; the background "
                  "server starts within 30 seconds.")
        schedule.install_serve(settings)
        print(schedule.serve_status(settings))
    elif args.action == "uninstall":
        print("Stopped and removed the server at login." if schedule.uninstall_serve()
              else "The server at login wasn't installed.")
    elif args.action == "restart":
        print("Restarted the server." if schedule.restart_serve()
              else "The server at login isn't installed: `fintrack autostart install`.")
    else:
        print(schedule.serve_status(settings))


def _set_env(settings: Settings, name: str, value: str) -> None:
    """Set NAME=value in FINTRACK_HOME/.env (replacing an existing line, else appending), keeping the file mode 600."""
    path = settings.env_file
    lines = path.read_text().splitlines() if path.exists() else []
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == name:
            lines[i] = f"{name}={value}"
            break
    else:
        lines.append(f"{name}={value}")
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)


def cmd_remote(args) -> None:
    """Phone access with no app on the phone (remote.py, gate.py; README "Phone access")."""
    from fintrack import remote, schedule

    settings = load_settings(require_plaid=False)
    try:
        if args.action == "setup":
            if args.host:
                _set_env(settings, "REMOTE_HOST", args.host)
            if args.port:
                _set_env(settings, "REMOTE_PORT", str(args.port))
            settings = load_settings(require_plaid=False)
            remote.setup(settings)
            print(f"Phones will open {settings.remote_origin}")
            print(f"Certificate ready (expires {remote.certificate_expires(settings).date().isoformat()}).")
            print(f"Next: forward TCP port {settings.remote_port} on your router to this Mac, set a passcode "
                  "(fintrack remote passcode), add a phone (fintrack remote phone <name>), then: fintrack remote enable")
        elif args.action == "phone":
            if not args.name:
                sys.exit("Name the phone: fintrack remote phone <name>")
            _add_phone(settings, args.name)
        elif args.action == "remove":
            if not (args.name and remote.remove_phone(settings, args.name)):
                sys.exit(f"No phone named {args.name!r}. See: fintrack remote status")
            if schedule.restart_gate():
                print(f"Removed {args.name!r}; the gate restarted without it.")
            else:
                print(f"Removed {args.name!r}.")
        elif args.action == "passcode":
            import getpass

            first = getpass.getpass("New passcode for phones: ")
            if getpass.getpass("Again: ") != first:
                sys.exit("The two didn't match; nothing changed.")
            remote.set_passcode(settings, first)
            print("Passcode set. Phones signed in before now must enter it again.")
        elif args.action == "enable":
            _enable_remote(settings)
        elif args.action == "disable":
            schedule.uninstall_gate()
            _set_env(settings, "REMOTE_ACCESS", "off")
            print("Phone access off: the gate is stopped and removed.")
            print("Restarted the server." if schedule.restart_serve() else "Restart `fintrack serve` to finish.")
        else:
            _remote_status(settings)
    except remote.RemoteError as exc:
        sys.exit(str(exc))


def _add_phone(settings: Settings, name: str) -> None:
    """Issue the phone's certificate and hand it, with the authority's, to the phone once over the home Wi-Fi."""
    import segno

    from fintrack import remote, schedule

    p12, password = remote.add_phone(settings, name)
    ca = remote.ca_file(settings).read_bytes()
    files = {"fintrack-ca.crt": (ca, "application/x-x509-ca-cert"),
             f"{name.replace(' ', '-')}.p12": (p12, "application/x-pkcs12")}

    def ready(url: str) -> None:
        print(f"Phone {name!r}: with the phone on this Mac's Wi-Fi, open this page on it (or scan the code):\n")
        segno.make(url, error="m").terminal(compact=True)
        print(f"\n  {url}\n")
        print("Download both files and install them (README \"Phone access\"):")
        print("  fintrack-ca.crt   the server's authority. Its fingerprint (SHA-256) should read:")
        print(f"                    {remote.fingerprint(ca)}")
        print(f"  {name.replace(' ', '-')}.p12   this phone's certificate. Its password: {password}")
        print("The password and the file are shown once and saved nowhere. Waiting up to 10 minutes (Ctrl+C to stop)...")

    sent = remote.offer_once(files, ready)
    print("Both files downloaded." if sent == set(files) else
          f"Not downloaded: {', '.join(sorted(set(files) - sent))}. Run this again for a new certificate.")
    if schedule.restart_gate():
        print("The gate restarted with this phone.")


def _enable_remote(settings: Settings) -> None:
    from fintrack import remote, schedule

    if not remote.sandbox_available():
        sys.exit("/usr/bin/sandbox-exec isn't available, so the gate can't be sandboxed. Phone access stays off.")
    missing = []
    if remote.certificate_expires(settings) is None:
        missing.append("a certificate (fintrack remote setup --host <your home's public IP>)")
    if not remote.phones(settings):
        missing.append("a phone (fintrack remote phone <name>)")
    if not remote.passcode_set(settings):
        missing.append("a passcode (fintrack remote passcode)")
    if missing:
        sys.exit("Phone access needs " + ", ".join(missing) + ".")
    remote.write_gate_profile(settings)
    _set_env(settings, "REMOTE_ACCESS", "on")
    settings = load_settings(require_plaid=False)
    schedule.install_gate(settings)
    print(schedule.gate_status(settings))
    print("Restarted the server." if schedule.restart_serve() else "Restart `fintrack serve` to finish.")
    print(f"Phones open {settings.remote_origin}. If macOS asks whether Python may accept incoming connections, "
          "choose Allow.")


def _remote_status(settings: Settings) -> None:
    from fintrack import remote, schedule

    expires = remote.certificate_expires(settings)
    print(f"Phone access: {'on' if settings.remote_access else 'off'} (REMOTE_ACCESS in .env)")
    print(f"Phones open: {settings.remote_origin if settings.remote_host else 'not set (fintrack remote setup --host ...)'}")
    print(f"Router: forward TCP {settings.remote_port} to this Mac (its home address: {remote.lan_address() or 'none'})")
    print(f"Certificate: {'expires ' + expires.date().isoformat() if expires else 'none (fintrack remote setup)'}")
    if expires:
        print(f"Authority fingerprint (SHA-256): {remote.fingerprint(remote.ca_file(settings).read_bytes())}")
    print(f"Passcode: {'set' if remote.passcode_set(settings) else 'not set (fintrack remote passcode)'}")
    listed = remote.phones(settings)
    print("Phones: " + (", ".join(f"{p['name']} (added {p['added']})" for p in listed) if listed else "none"))
    print("Gate: " + ("\n  ".join(schedule.gate_status(settings).splitlines()) if schedule.gate_installed()
                     else "not installed (fintrack remote enable)"))


def cmd_demo(args) -> None:
    """Fill the SANDBOX database with made-up accounts and activity (demo.py). Never runs against Production.

    An empty database is filled directly. One that already has data is refused unless --replace, which
    first backs it up to ~/.fintrack/backups, then starts a fresh database."""
    from fintrack import demo
    from fintrack.crypto import TokenCipher
    from fintrack.logging_setup import configure_logging
    from fintrack.quotes import QuoteGateway

    settings = load_settings()
    if settings.is_production:
        sys.exit("Demo data is for the Sandbox only, and PLAID_ENV is production. Nothing was changed.")
    configure_logging(settings.logs_dir, secrets=settings.secrets)

    has_data = False
    if settings.db_path.exists():
        probe = db.connect(settings.db_path)
        try:
            has_data = any(probe.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("items", "accounts")
                           ) if probe.execute("SELECT 1 FROM sqlite_master WHERE name = 'items'").fetchone() else False
        finally:
            probe.close()
    if has_data:
        if not args.replace:
            sys.exit(f"{settings.db_path.name} already has connections or accounts. Run `fintrack demo --replace` to back it up "
                     "to the backups folder and start the demo fresh.")
        print(f"Backed up {settings.db_path.name} to {backup_database(settings.db_path, settings.backups_dir)}")
        for suffix in ("", "-wal", "-shm"):
            Path(str(settings.db_path) + suffix).unlink(missing_ok=True)

    _migrate(settings, quiet=True)
    quotes = QuoteGateway(settings.alpaca_key_id, settings.alpaca_secret_key) if settings.quotes_enabled else None
    conn = db.connect(settings.db_path)
    try:
        try:
            summary = demo.seed(conn, settings, TokenCipher(settings.home), quotes=quotes)
        except demo.DemoError as exc:
            sys.exit(str(exc))
    finally:
        conn.close()
    print(f"Demo data ready in {settings.db_path.name}: {summary['items']} connections, {summary['plaid_accounts']} Plaid-style "
          f"accounts + {summary['manual_accounts']} manual accounts, {summary['transactions']:,} transactions since {summary['since']}.")
    print(f"Net worth: ${summary['net_worth_cents'] / 100:,.0f}. Restart `fintrack serve` to see it"
          + ("" if quotes else " (no Alpaca keys: prices are approximate and history is flat)") + ".")


def cmd_serve(_args) -> None:
    import uvicorn

    from fintrack.logging_setup import configure_logging
    from fintrack.web.app import create_app

    settings = load_settings(require_plaid=False)
    if not (settings.cert_file.exists() and settings.key_file.exists()):
        sys.exit("No HTTPS certificate found. Run `fintrack certs` first.")
    _migrate(settings)
    configure_logging(settings.logs_dir, secrets=settings.secrets)

    app = create_app(settings)
    # Phone access (REMOTE_ACCESS=on): a second listener for the gate, on loopback too (127.0.0.1:REMOTE_INNER_PORT),
    # in its own thread; it stops when this main server stops (remote.run_phone_listener).
    stop_phones = threading.Event()
    if settings.remote_access:
        from fintrack import remote

        threading.Thread(target=remote.run_phone_listener, args=(app, settings, stop_phones),
                         name="fintrack-phones", daemon=True).start()
        print(f"Phone access: {settings.remote_origin} through the gate (fintrack remote status)")

    print(f"fintrack running at {settings.base_url}  (Ctrl+C to stop)")
    try:
        uvicorn.run(
            app,
            host=HOST,  # loopback only, never 0.0.0.0
            port=settings.port,
            ssl_certfile=str(settings.cert_file),
            ssl_keyfile=str(settings.key_file),
            log_config=None,  # use our redacting log handlers
            server_header=False,
        )
    finally:
        stop_phones.set()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="fintrack", description="Local personal finance tracker.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, func, help_text in [
        ("init", cmd_init, "create the data folder, .env, Keychain key, and database"),
        ("certs", cmd_certs, "create the localhost HTTPS certificate with mkcert"),
        ("serve", cmd_serve, "start the web app"),
        ("migrate", cmd_migrate, "apply database migrations"),
        ("backup", cmd_backup, "copy the database to a dated backup file"),
        ("items", cmd_items, "show Plaid Items and the Production Item budget"),
        ("sync", cmd_sync, "sync transactions and balances for all connections"),
        ("preflight", cmd_preflight, "check the Production setup before connecting real banks (creates no Item)"),
    ]:
        commands.add_parser(name, help=help_text).set_defaults(func=func)

    from fintrack.schedule import DEFAULT_TIMES

    sched = commands.add_parser("schedule", help="manage the scheduled sync (macOS launchd)")
    sched.add_argument("action", choices=["install", "uninstall", "status"])
    sched.add_argument("--times", default=DEFAULT_TIMES, help=f"daily times, HH:MM,HH:MM (default {DEFAULT_TIMES})")
    sched.set_defaults(func=cmd_schedule)

    auto = commands.add_parser("autostart", help="run the web server at login, restarted if it crashes (macOS launchd)")
    auto.add_argument("action", choices=["install", "uninstall", "status", "restart"])
    auto.set_defaults(func=cmd_autostart)

    rem = commands.add_parser("remote", help="phone access with no app on the phone (README \"Phone access\")")
    rem.add_argument("action", choices=["setup", "phone", "remove", "passcode", "enable", "disable", "status"])
    rem.add_argument("name", nargs="?", help="phone / remove: the phone's name, e.g. pixel")
    rem.add_argument("--host", help="setup: your home's public IP address or hostname (from your router's page)")
    rem.add_argument("--port", type=int, help="setup: the public port phones use (default 48443)")
    rem.set_defaults(func=cmd_remote)

    demo_parser = commands.add_parser("demo", help="Sandbox only: fill the database with made-up demo accounts and activity")
    demo_parser.add_argument("--replace", action="store_true",
                             help="if the Sandbox database already has data: back it up, then start the demo fresh")
    demo_parser.set_defaults(func=cmd_demo)

    imp = commands.add_parser("import", aliases=["import-csv"],
                              help="bank files (CSV, Excel, OFX/QFX/QBO, QIF) into an account (connected or manual): preview, --apply, --history, --undo")
    imp.add_argument("files", nargs="*", help="the downloaded file(s); each is its own import")
    imp.add_argument("--account", type=int, help="the account's id (the number in /accounts/<id>)")
    imp.add_argument("--apply", action="store_true", help="write it (default: only show what would happen)")
    imp.add_argument("--overlap", choices=["outside", "skip", "review"],
                     help="when the file's dates overlap the account's: only dates it doesn't have / every row but "
                          "likely duplicates / like skip, plus the duplicates named with --take")
    imp.add_argument("--take", help="with --overlap review: rows of likely duplicates to import anyway, e.g. 12,15")
    imp.add_argument("--dates", choices=["mdy", "dmy"], help="month first or day first, when the file can't tell")
    imp.add_argument("--money-out", choices=["positive", "negative"],
                     help="how the file writes money out, when it has one signed amount column")
    imp.add_argument("--columns", help="what a column holds, if a guess is wrong: e.g. date=1,description=3,amount=5 "
                                       "(roles: date, authorized, description, amount, debit, credit, direction, balance, "
                                       "card, category)")
    imp.add_argument("--before", help="only rows posted before this date")
    imp.add_argument("--primary-card", metavar="LAST4", help="which card in the file is the account's own")
    imp.add_argument("--kind", choices=["transactions", "activity", "positions"],
                     help="what the file holds, if the guess is wrong (activity: buys, sells...; positions: holdings)")
    imp.add_argument("--as-of", metavar="DATE", help="a holdings statement's date, when the file doesn't say")
    imp.add_argument("--added-dividends", dest="added_dividends", action="store_true", default=None,
                     help="add a dividend for each reinvestment the file has none for (the default)")
    imp.add_argument("--no-added-dividends", dest="added_dividends", action="store_false",
                     help="don't add those dividends")
    imp.add_argument("--holding", action="append", default=[], metavar="SYMBOL=TICKER",
                     help="a manual account: the line a fund goes to (another ticker = tracked by its dollars, a proxy;"
                          " SYMBOL=new: a line of its own); repeat for several")
    imp.add_argument("--history", action="store_true", help="list past imports (with --account: that account's)")
    imp.add_argument("--undo", type=int, metavar="BATCH", help="remove everything one import added (with --apply)")
    imp.set_defaults(func=cmd_import)
    conv = commands.add_parser("convert-positions",
                               help="manual investment accounts' entries into activity, checked on a copy first")
    conv.add_argument("--apply", action="store_true", help="convert the real database (only when the copy matches)")
    conv.set_defaults(func=cmd_convert_positions)
    desc = commands.add_parser("descriptions", help="raw bank text for destination rules (--backfill: fill in history once)")
    desc.add_argument("--backfill", action="store_true", help="fill in the raw bank text for existing transactions")
    desc.add_argument("--dry-run", action="store_true", help="with --backfill: only count, write nothing")
    desc.set_defaults(func=cmd_descriptions)
    loc = commands.add_parser("locations", help="where transactions happened (--backfill: fill in history once)")
    loc.add_argument("--backfill", action="store_true", help="fill in locations for existing transactions from Plaid")
    loc.add_argument("--dry-run", action="store_true", help="with --backfill: only count, write nothing")
    loc.add_argument("--item", type=int, help="with --backfill: only this connection (its Item id, `fintrack items`)")
    loc.set_defaults(func=cmd_locations)
    logo = commands.add_parser("logos", help="merchant logos: download missing ones (--backfill: fill in history once)")
    logo.add_argument("--backfill", action="store_true", help="fill in logo data for existing transactions from Plaid")
    logo.add_argument("--dry-run", action="store_true", help="with --backfill: only count, write nothing")
    logo.set_defaults(func=cmd_logos)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ConfigError as exc:
        sys.exit(f"Configuration error: {exc}")


if __name__ == "__main__":
    main()
