"""Per-account submission pacing — pick_for_submission + mark_used.

With one account at INTER_DELAY=120s, dispatch waits 120s between every
attempt. With four accounts, the same INTER_DELAY can be served in
parallel (round-robin), shrinking the effective inter-submission cap by
~4×. These tests exercise the picker's selection and the wait-time
math; the dispatch.py integration is exercised indirectly via the
existing test_dispatch_warning suite which runs full POST→dispatch
flows."""
import datetime as dt
import pytest

from app import leetcode_accounts as _lc


@pytest.fixture
def reset_pool(monkeypatch, tmp_path):
    """Per-test scratch pool file; block the .env bootstrap fallback so
    the pool is truly empty at test start."""
    p = tmp_path / "leetcode_accounts.json"
    monkeypatch.setattr(_lc, "ACCOUNTS_JSON", p)
    monkeypatch.setattr(_lc, "_bootstrap_from_env", lambda: [])
    return p


COOKIE = "LEETCODE_SESSION=eyJ_test_.fake; csrftoken=abc"


def test_pick_for_submission_returns_none_on_empty_pool(reset_pool):
    assert _lc.pick_for_submission(min_gap_s=120) is None


def test_pick_for_submission_prefers_never_used(reset_pool):
    """Fresh account (no last_used_at) is available IMMEDIATELY — wait
    must be 0.0, not the full min_gap_s."""
    _lc.add_account("fresh", COOKIE)
    pick = _lc.pick_for_submission(min_gap_s=120)
    assert pick is not None
    acc, wait = pick
    assert acc.name == "fresh"
    assert wait == 0.0


def test_pick_for_submission_round_robins_across_accounts(reset_pool):
    """With two accounts and a 120s gap, mark_used("a") then call
    pick → should return "b" (not "a", which still has 120s to go)."""
    _lc.add_account("a", COOKIE)
    _lc.add_account("b", COOKIE)
    _lc.mark_used("a")
    pick = _lc.pick_for_submission(min_gap_s=120)
    assert pick is not None
    chosen, wait = pick
    assert chosen.name == "b"
    assert wait == 0.0


def test_pick_for_submission_waits_when_all_recently_used(reset_pool):
    """With two accounts both used 30s ago and a 120s gap, the picker
    returns the account that's earliest available (a tie — either works)
    with a positive wait ≈ 90s."""
    _lc.add_account("a", COOKIE)
    _lc.add_account("b", COOKIE)
    thirty_ago = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=30)
    _lc.mark_used("a", when=thirty_ago)
    _lc.mark_used("b", when=thirty_ago)
    pick = _lc.pick_for_submission(min_gap_s=120)
    assert pick is not None
    chosen, wait = pick
    # 30s have elapsed since both were used; 120-30 = 90s remaining (±1s slop).
    assert 88 <= wait <= 92, f"expected ~90s, got {wait}"


def test_pick_for_submission_skips_exhausted(reset_pool):
    """Exhausted account (24h cooldown after a 429) is NEVER picked, even
    if it's been idle the longest."""
    _lc.add_account("a", COOKIE)
    _lc.add_account("b", COOKIE)
    _lc.mark_exhausted("a", hours=1)         # `a` is parked
    _lc.mark_used("b")                       # `b` just used
    # Even though `b` has 120s of cooldown left, it should win because
    # `a` is exhausted and ineligible.
    pick = _lc.pick_for_submission(min_gap_s=120)
    assert pick is not None
    chosen, wait = pick
    assert chosen.name == "b"
    assert wait > 0    # waiting on b's cooldown


def test_mark_used_persists_across_reload(reset_pool):
    """The last_used_at field must survive being written to the JSON
    file and read back."""
    _lc.add_account("primary", COOKIE)
    _lc.mark_used("primary")
    # Re-read from disk to confirm round-trip.
    reread = next(a for a in _lc.load_accounts() if a.name == "primary")
    assert reread.last_used_at is not None
    parsed = dt.datetime.fromisoformat(reread.last_used_at)
    now = dt.datetime.now(dt.timezone.utc)
    assert abs((now - parsed).total_seconds()) < 5


def test_mark_used_unknown_name_is_noop(reset_pool):
    """Race: an account could be removed between pick and mark. mark_used
    on an unknown name must not crash or write garbage to the file."""
    _lc.add_account("real", COOKIE)
    _lc.mark_used("ghost")                   # not in pool
    # Real account untouched.
    real = next(a for a in _lc.load_accounts() if a.name == "real")
    assert real.last_used_at is None


def test_pick_account_by_name(reset_pool):
    """The lookup honours the dispatcher's forced-account env var; absent
    or unusable names return None so leetbot can fall back."""
    _lc.add_account("primary", COOKIE)
    assert _lc.pick_account_by_name("primary").name == "primary"
    assert _lc.pick_account_by_name("missing") is None
