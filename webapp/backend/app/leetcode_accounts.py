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


def _resolve_accounts_json() -> Path:
    """Pick the credentials file path. Priority:
      1. ARENA_LEETCODE_ACCOUNTS env var — explicit override for ops.
      2. <repo_root>/data/leetcode_accounts.json — the source-tree layout
         (webapp/backend/app/… → .parent×4 lands on the repo root).
      3. /data/leetcode_accounts.json — the container layout, where the
         persistent volume lives at /data and the source tree is /app.

    Returns the first existing candidate, or — when nothing exists yet —
    the first writable candidate so `--add` lands in a sensible place.
    """
    override = os.environ.get("ARENA_LEETCODE_ACCOUNTS")
    if override:
        return Path(override)
    src_layout = Path(__file__).parent.parent.parent.parent / "data" / "leetcode_accounts.json"
    container_layout = Path("/data/leetcode_accounts.json")
    # Prefer whichever EXISTS so an in-image install picks up the volume,
    # and a source-tree install picks up the gitignored host file.
    for cand in (src_layout, container_layout):
        if cand.exists():
            return cand
    # Nothing exists yet — choose by writability of the parent so `--add`
    # works on both layouts without surprising the user.
    for cand in (src_layout, container_layout):
        if cand.parent.exists() and os.access(cand.parent, os.W_OK):
            return cand
    return src_layout  # last-resort default; _save_raw will mkdir(parents=True)


ACCOUNTS_JSON = _resolve_accounts_json()
REPO_ROOT = ACCOUNTS_JSON.parent.parent          # kept for any importers that referenced it

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
    # When the dispatcher last submitted with this account. Drives the
    # per-account inter-submission gap (see pick_for_submission below):
    # an account that just submitted needs to cool down before its next
    # use, but a SIBLING account can be used immediately. This is how
    # multi-account pools translate into wall-time savings on a sweep.
    last_used_at: str | None = None      # ISO 8601 UTC, or None

    def is_exhausted(self, *, now: dt.datetime | None = None) -> bool:
        if not self.exhausted_until:
            return False
        try:
            t = dt.datetime.fromisoformat(self.exhausted_until)
        except ValueError:
            return False
        return (now or dt.datetime.now(dt.timezone.utc)) < t

    def next_available_at(self, min_gap_s: float,
                          *, now: dt.datetime | None = None) -> dt.datetime:
        """Earliest time this account can be used again, considering BOTH:
          * the 24 h-ish exhaustion cooldown (after a 429), and
          * the per-account minimum gap between submissions (`min_gap_s`).
        Never returns a time in the past — clamped to `now` so callers
        don't have to handle negative wait times."""
        now = now or dt.datetime.now(dt.timezone.utc)
        candidates: list[dt.datetime] = []
        if self.exhausted_until:
            try:
                candidates.append(dt.datetime.fromisoformat(self.exhausted_until))
            except ValueError:
                pass
        if self.last_used_at:
            try:
                last = dt.datetime.fromisoformat(self.last_used_at)
                candidates.append(last + dt.timedelta(seconds=min_gap_s))
            except ValueError:
                pass
        if not candidates:
            return now
        nxt = max(candidates)
        return nxt if nxt > now else now

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
            last_used_at=r.get("last_used_at"),
        ))
    return out


def pick_account() -> Account | None:
    """Return the first non-exhausted account, or None if the pool is dry."""
    for a in load_accounts():
        if not a.is_exhausted() and a.cookie_header:
            return a
    return None


def pick_account_by_name(name: str) -> Account | None:
    """Look up a specific account by name; returns None when unknown or
    when its cookies are missing. Used by leetbot.py to honour the
    `LEETCODE_FORCE_ACCOUNT` env var the dispatcher sets when it wants a
    specific credential for the next submission (round-robin pacing)."""
    for a in load_accounts():
        if a.name == name and a.cookie_header:
            return a
    return None


def pick_for_submission(min_gap_s: float) -> tuple[Account, float] | None:
    """Pick the next account for an outgoing submission, optimising for
    minimum wait time.

    Strategy: of all non-exhausted accounts, pick the one whose
    `next_available_at(min_gap_s)` is soonest. Returns
    `(account, wait_seconds)` — the caller sleeps `wait_seconds` (0 if
    the account is ready now) before submitting with it, then calls
    `mark_used(account.name)` after the submission lands.

    Why this design works: with N accounts in the pool, the effective
    inter-submission cap shrinks by ~N×. If account A submitted 30 s
    ago and account B is fresh, the picker returns (B, 0) — we don't
    pay A's remaining 90 s of cooldown. With 4 accounts and a 120 s
    cap, a sweep can submit every ~30 s instead of every 120 s.

    Returns None if the pool is empty or every account is exhausted."""
    accounts = [a for a in load_accounts()
                if a.cookie_header and not a.is_exhausted()]
    if not accounts:
        return None
    now = dt.datetime.now(dt.timezone.utc)
    # Sort key: (next_available_at, last_used_at_or_epoch). The first
    # axis picks the account whose cooldown clears soonest; the second
    # axis breaks ties so a never-used account (last_used_at = None →
    # treated as epoch) wins over a recently-used one when they both
    # report "ready now". Without the tiebreaker, `min()` returns
    # whatever order the file lists them in, which means the first
    # entry monopolises every dispatch under a fresh multi-account pool.
    _EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    def _last_used(a: Account) -> dt.datetime:
        if not a.last_used_at:
            return _EPOCH
        try:
            t = dt.datetime.fromisoformat(a.last_used_at)
            if t.tzinfo is None:
                t = t.replace(tzinfo=dt.timezone.utc)
            return t
        except ValueError:
            return _EPOCH
    best = min(
        accounts,
        key=lambda a: (a.next_available_at(min_gap_s, now=now), _last_used(a)),
    )
    wait = max(0.0, (best.next_available_at(min_gap_s, now=now) - now).total_seconds())
    return best, wait


def mark_used(name: str, *, when: dt.datetime | None = None) -> None:
    """Update `last_used_at` for one account. Called by the dispatcher
    after every leetbot.py subprocess returns, regardless of verdict —
    the cooldown is about RATE LIMITING, not success, so failed
    submissions also count against the per-account budget.

    No-op when `name` isn't in the pool — defends against a race where
    an account was removed between pick and mark."""
    when = when or dt.datetime.now(dt.timezone.utc)
    iso = when.isoformat()
    with _lock:
        rows = _load_raw()
        hit = False
        for r in rows:
            if r.get("name") == name:
                r["last_used_at"] = iso
                hit = True
                break
        if hit:
            _save_raw(rows)


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
             "is_exhausted": a.is_exhausted(),
             "last_used_at": a.last_used_at}
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
