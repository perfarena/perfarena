"""LeetCode credential pool with daily-cap rotation.

LeetCode enforces a per-account submission cap (currently ~15 successful
submissions per day for free accounts; more if you've been around). Hitting
it returns HTTP 429 and the account is unusable for the rest of the day.

To populate the system at scale we keep a pool of accounts and rotate to
the next non-exhausted one on 429. State lives in
`data/leetcode_accounts.json` (git-ignored — it holds session cookies):

    [
      {
        "name": "primary",
        "cookie_header": "LEETCODE_SESSION=...; csrftoken=...",
        "exhausted_until": null
      },
      ...
    ]

`exhausted_until` is an ISO-8601 UTC timestamp; the account is skipped
until clock time passes it. On a 429 we set `exhausted_until = now + 24h`
(LeetCode's daily caps reset at roughly 24h granularity).

Bootstrap path: the first call to `load_accounts()` migrates a single-
account `.env` (LEETCODE_COOKIE_HEADER or LEETCODE_SESSION+CSRF) into
`accounts.json` under the name "primary", so existing single-account
setups keep working without any manual JSON-editing.

CLI:
    python -m app.leetcode_accounts                    # show pool status
    python -m app.leetcode_accounts --add NAME --cookie 'LEETCODE_SESSION=...; csrftoken=...'
    python -m app.leetcode_accounts --whoami           # probe each account
    python -m app.leetcode_accounts --clear-exhausted  # reset exhaustion flags
    python -m app.leetcode_accounts --remove NAME
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import httpx
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).parent.parent.parent.parent
ACCOUNTS_JSON = REPO_ROOT / "data" / "leetcode_accounts.json"

# Per-account cooldown after a 429. LeetCode's per-day cap resets roughly
# daily; we round up to 24h to stay safely on the right side of the window.
# LeetCode returns 429 for two very different reasons, and we can't easily
# tell them apart from the response alone:
#   1. Daily-cap exhausted (true ~24 h cooldown until midnight UTC reset).
#   2. Per-IP transient rate-limit (clears in minutes — single-machine bursts
#      across multiple accounts trip this trivially because LC also rate-
#      limits by source IP, not just by account).
#
# Assuming (1) on every 429 burns a full day of capacity for what's usually
# (2). Default to a short cooldown — long enough that we don't immediately
# re-hit the rate-limit, short enough that we recover quickly if it was
# transient. Callers can override (the bootstrap script's --no-retry path
# already wraps this with its own outer backoff).
EXHAUSTION_HOURS = 0.5    # 30 min — short cooldown for transient 429s

# Process-local lock around the file. Cross-process safety relies on the
# atomic-rename write pattern (mark_exhausted reads, mutates, atomically
# renames into place — last writer wins; we never partial-write).
_lock = threading.Lock()


@dataclass
class Account:
    name: str
    cookie_header: str
    exhausted_until: str | None = None   # ISO 8601 UTC, or None

    def is_exhausted(self, *, now: dt.datetime | None = None) -> bool:
        if not self.exhausted_until:
            return False
        try:
            t = dt.datetime.fromisoformat(self.exhausted_until)
        except ValueError:
            return False
        return (now or dt.datetime.now(dt.timezone.utc)) < t

    def parsed_cookies(self) -> tuple[str, str]:
        """Return (LEETCODE_SESSION, csrftoken) parsed from cookie_header.

        Raises ValueError if either is missing — the account is unusable
        and the caller should surface that rather than send empty cookies.
        """
        jar: dict[str, str] = {}
        for piece in self.cookie_header.split(";"):
            if "=" in piece:
                k, _, v = piece.strip().partition("=")
                jar[k.strip()] = v.strip()
        session = jar.get("LEETCODE_SESSION", "")
        csrf = jar.get("csrftoken", "")
        if not session or not csrf:
            raise ValueError(
                f"account {self.name!r} cookie_header missing "
                f"LEETCODE_SESSION or csrftoken"
            )
        return session, csrf


# ---------- file I/O --------------------------------------------------------

def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _load_raw() -> list[dict]:
    if not ACCOUNTS_JSON.exists():
        return []
    try:
        return json.loads(ACCOUNTS_JSON.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save_raw(rows: list[dict]) -> None:
    """Atomic write — tmp + rename — so a concurrent reader never sees a
    half-written file."""
    ACCOUNTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = ACCOUNTS_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, indent=2))
    tmp.replace(ACCOUNTS_JSON)


def _bootstrap_from_env() -> list[dict]:
    """First-time migration: seed accounts.json from .env if it has creds.

    Existing single-account setups keep working without manual editing —
    the first `load_accounts()` call materialises the file.
    """
    load_dotenv(REPO_ROOT / ".env")
    cookie_header = os.getenv("LEETCODE_COOKIE_HEADER")
    if not cookie_header:
        session = os.getenv("LEETCODE_SESSION")
        csrf = os.getenv("LEETCODE_CSRF_TOKEN")
        if session and csrf:
            cookie_header = f"LEETCODE_SESSION={session}; csrftoken={csrf}"
    if not cookie_header:
        return []
    rows = [{
        "name": "primary",
        "cookie_header": cookie_header,
        "exhausted_until": None,
    }]
    _save_raw(rows)
    return rows


# ---------- public API ------------------------------------------------------

def load_accounts() -> list[Account]:
    """Return all accounts in pool order. Lazily migrates from .env on first
    use; never raises (returns [] when no creds exist anywhere)."""
    rows = _load_raw()
    if not rows:
        rows = _bootstrap_from_env()
    out: list[Account] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append(Account(
            name=r.get("name") or "?",
            cookie_header=r.get("cookie_header", ""),
            exhausted_until=r.get("exhausted_until"),
        ))
    return out


def pick_account() -> Account | None:
    """Return the first non-exhausted account, or None if the pool is dry."""
    for a in load_accounts():
        if not a.is_exhausted() and a.cookie_header:
            return a
    return None


def mark_exhausted(name: str, *, hours: float = EXHAUSTION_HOURS) -> None:
    """Atomically mark this account exhausted for `hours` from now.

    Safe to call from anywhere; reads the live file, updates the matching
    row, atomically renames. No-op if the named account isn't in the file
    (e.g. someone deleted it between pick and mark).
    """
    with _lock:
        rows = _load_raw()
        until = (_now_utc() + dt.timedelta(hours=hours)).isoformat()
        hit = False
        for r in rows:
            if r.get("name") == name:
                r["exhausted_until"] = until
                hit = True
                break
        if hit:
            _save_raw(rows)


def clear_exhaustion(name: str | None = None) -> int:
    """Clear `exhausted_until` on one account (by name) or all (name=None).
    Returns the number of rows actually changed. Useful when you know an
    account has rolled over but the timestamp hasn't expired yet."""
    with _lock:
        rows = _load_raw()
        changed = 0
        for r in rows:
            if name is not None and r.get("name") != name:
                continue
            if r.get("exhausted_until") is not None:
                r["exhausted_until"] = None
                changed += 1
        if changed:
            _save_raw(rows)
        return changed


def pool_status() -> dict:
    """Diagnostic snapshot of the pool — counts plus per-account state."""
    accs = load_accounts()
    available = [a for a in accs if not a.is_exhausted()]
    exhausted = [a for a in accs if a.is_exhausted()]
    earliest = None
    if exhausted:
        try:
            times = [dt.datetime.fromisoformat(a.exhausted_until)  # type: ignore[arg-type]
                     for a in exhausted if a.exhausted_until]
            earliest = min(times) if times else None
        except ValueError:
            earliest = None
    return {
        "total": len(accs),
        "available": len(available),
        "exhausted": len(exhausted),
        "earliest_available_after": earliest.isoformat() if earliest else None,
        "accounts": [
            {"name": a.name, "exhausted_until": a.exhausted_until,
             "is_exhausted": a.is_exhausted()}
            for a in accs
        ],
    }


def pool_summary_line() -> str:
    """One-line human-readable summary for the bootstrap-script planning
    output. Includes the soonest recovery time when something is out."""
    s = pool_status()
    if s["total"] == 0:
        return "credential pool: EMPTY (no .env LEETCODE_* and no data/leetcode_accounts.json)"
    parts = [f"{s['total']} account(s), {s['available']} available"]
    if s["exhausted"]:
        when = s["earliest_available_after"]
        if when:
            remaining = (dt.datetime.fromisoformat(when) - _now_utc()).total_seconds() / 3600
            parts.append(f"{s['exhausted']} exhausted (earliest rolls over in ~{remaining:.1f}h)")
        else:
            parts.append(f"{s['exhausted']} exhausted")
    return "credential pool: " + " — ".join(parts)


def add_account(name: str, cookie_header: str) -> None:
    """Append/overwrite an account by name."""
    with _lock:
        rows = _load_raw()
        for r in rows:
            if r.get("name") == name:
                r["cookie_header"] = cookie_header
                r["exhausted_until"] = None
                _save_raw(rows)
                return
        rows.append({"name": name, "cookie_header": cookie_header,
                     "exhausted_until": None})
        _save_raw(rows)


def remove_account(name: str) -> bool:
    with _lock:
        rows = _load_raw()
        kept = [r for r in rows if r.get("name") != name]
        if len(kept) == len(rows):
            return False
        _save_raw(kept)
        return True


# ---------- whoami probe (for the CLI) --------------------------------------

_USER_STATUS_QUERY = "query globalData { userStatus { isSignedIn username } }"


def whoami(account: Account, *, host: str | None = None,
           timeout: float = 15.0) -> tuple[bool, str]:
    """Hit GraphQL `userStatus` with the account's cookies. Returns
    (is_signed_in, info-line). Used by the CLI to verify pool health."""
    host = host or os.getenv("LEETCODE_HOST", "https://leetcode.com")
    try:
        session, csrf = account.parsed_cookies()
    except ValueError as e:
        return False, str(e)
    try:
        with httpx.Client(
            base_url=host,
            cookies={"LEETCODE_SESSION": session, "csrftoken": csrf},
            headers={
                "User-Agent": "perfarena-credential-probe/1.0",
                "x-csrftoken": csrf,
                "Content-Type": "application/json",
                "Origin": host,
                "Referer": host + "/",
            },
            timeout=timeout,
        ) as c:
            r = c.post("/graphql", json={
                "query": _USER_STATUS_QUERY,
                "operationName": "globalData",
                "variables": {},
            })
    except httpx.HTTPError as e:
        return False, f"network error: {e}"
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}"
    try:
        us = r.json()["data"]["userStatus"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return False, f"unparseable response: {r.text[:120]!r}"
    if us.get("isSignedIn"):
        return True, f"signed in as {us.get('username') or '?'}"
    return False, "not signed in — cookies expired"


# ---------- CLI -------------------------------------------------------------

def _cli() -> int:
    ap = argparse.ArgumentParser(
        description="Manage the LeetCode credential pool.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("CLI:")[-1] if __doc__ else None,
    )
    ap.add_argument("--add", metavar="NAME",
                    help="add (or update) an account by name; pair with --cookie")
    ap.add_argument("--cookie", metavar="HEADER",
                    help="cookie header for --add, e.g. "
                         "'LEETCODE_SESSION=…; csrftoken=…'")
    ap.add_argument("--remove", metavar="NAME", help="remove an account by name")
    ap.add_argument("--clear-exhausted", metavar="NAME", nargs="?",
                    const="__all__",
                    help="clear exhaustion timestamps (give a NAME to target one, "
                         "no value to clear all)")
    ap.add_argument("--whoami", action="store_true",
                    help="probe each account's userStatus and print sign-in result")
    args = ap.parse_args()

    if args.add:
        if not args.cookie:
            ap.error("--add requires --cookie")
        add_account(args.add, args.cookie)
        print(f"added/updated account {args.add!r}")
        return 0

    if args.remove:
        ok = remove_account(args.remove)
        print(f"{'removed' if ok else 'no such account:'} {args.remove!r}")
        return 0

    if args.clear_exhausted is not None:
        target = None if args.clear_exhausted == "__all__" else args.clear_exhausted
        n = clear_exhaustion(target)
        scope = "all accounts" if target is None else f"account {target!r}"
        print(f"cleared exhaustion on {n} row(s) for {scope}")
        return 0

    accs = load_accounts()
    if not accs:
        print("(pool empty — no data/leetcode_accounts.json and no .env creds)")
        print("add one with: python -m app.leetcode_accounts --add NAME --cookie 'LEETCODE_SESSION=...; csrftoken=...'")
        return 0

    print(pool_summary_line())
    for a in accs:
        line = f"  - {a.name:<16}"
        if a.is_exhausted():
            line += f"  EXHAUSTED until {a.exhausted_until}"
        else:
            line += "  available"
        if args.whoami:
            ok, info = whoami(a)
            line += f"  ·  whoami: {'OK' if ok else 'FAIL'} ({info})"
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
