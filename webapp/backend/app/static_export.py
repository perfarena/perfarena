"""Static snapshot of the public API, for the read-only GitHub Pages build.

The snapshot frontend (VITE_STATIC=1, frontend/src/lib/staticMode.ts) has no
backend. This module answers every public GET request that its pages can send
and writes each answer to <out>/data/<key>.json. <key> is the start of the
SHA-256 of the canonical request (the decoded path and the sorted query pairs, as
JSON); staticMode.ts computes the same key from the URL that a page requests.

The requests go through the app itself (FastAPI's TestClient), as an anonymous
visitor, so the snapshot holds exactly what the live site shows to the public.
The app runs on a copy of the database, because some answers are computed on
request and stored (the efficiency rankings). The startup hook does not run, so
no dispatch, scheduler, or refresh thread starts.

Run it with the environment of the live server, because some settings change the
public answers (for example ARENA_LEADERBOARD_GATE):

    python -m app.static_export --db /data/arena.db --out /tmp/pages-snapshot
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit


def canonical(url: str) -> str:
    """The request as staticMode.ts canonical() sees it."""
    u = urlsplit(url)
    pairs = sorted(parse_qsl(u.query, keep_blank_values=True))
    return json.dumps([unquote(u.path), [list(p) for p in pairs]],
                      separators=(",", ":"), ensure_ascii=False)


def key(url: str) -> str:
    return hashlib.sha256(canonical(url).encode()).hexdigest()[:24]


def seg(s: str) -> str:
    """A path segment as encodeURIComponent writes it."""
    return quote(str(s), safe="-_.!~*'()")


def qs(pairs) -> str:
    """A query string as URLSearchParams writes it (pairs in the given order)."""
    return urlencode(list(pairs))


class Exporter:
    def __init__(self, client, out: Path):
        self.client, self.data = client, out / "data"
        self.data.mkdir(parents=True, exist_ok=True)
        self.done: dict[str, int] = {}          # key -> HTTP status
        self.failed: list[tuple[str, int]] = []
        self.bytes = 0

    def get(self, url: str):
        """The JSON answer to url, written to the snapshot; None when the app does
        not answer it with 200."""
        k = key(url)
        if k in self.done:
            p = self.data / f"{k}.json"
            return json.loads(p.read_text()) if self.done[k] == 200 else None
        r = self.client.get(url)
        self.done[k] = r.status_code
        if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
            self.failed.append((url, r.status_code))
            return None
        (self.data / f"{k}.json").write_bytes(r.content)
        self.bytes += len(r.content)
        if len(self.done) % 200 == 0:
            print(f"  {len(self.done)} requests, {self.bytes / 1e6:.0f} MB", flush=True)
        return r.json()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", required=True, help="the live database (it is copied, never written)")
    ap.add_argument("--out", required=True, help="output folder; data/ is created in it")
    args = ap.parse_args()
    out = Path(args.out)
    if (out / "data").exists():
        shutil.rmtree(out / "data")

    work = Path(tempfile.mkdtemp(prefix="pages-export-"))
    db = work / "arena.db"
    # The sqlite3 backup API gives a consistent copy even while the server writes.
    import sqlite3
    src = sqlite3.connect(f"file:{Path(args.db).resolve()}?mode=ro", uri=True)
    dst = sqlite3.connect(db)
    src.backup(dst)
    src.close()
    dst.close()
    os.environ["ARENA_DB"] = str(db)
    os.environ["ARENA_DISPATCH_ENABLED"] = "0"
    os.environ["ARENA_RANKING_REFRESH"] = "0"
    os.environ["ARENA_IP_RATE_LIMIT_ENABLED"] = "0"

    from fastapi.testclient import TestClient
    from .db import init_db
    init_db()                                    # the additive migrations, on the copy
    from .main import app

    t0 = time.time()
    ex = Exporter(TestClient(app), out)
    try:
        export_all(ex)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    ok = sum(1 for s in ex.done.values() if s == 200)
    (out / "data" / "meta.json").write_text(json.dumps({
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "requests": ok}))
    print(f"wrote {ok} answers ({ex.bytes / 1e6:.1f} MB) to {out / 'data'} in {time.time() - t0:.0f} s")
    if ex.failed:
        print(f"{len(ex.failed)} requests without a 200 answer (not in the snapshot):")
        for url, status in ex.failed[:40]:
            print(f"  {status} {url}")
    return 0


# The languages that the Rankings page offers (frontend pages/Rankings.tsx LANGS).
LANGS = ("python3", "java", "cpp", "javascript", "typescript", "csharp", "c", "golang",
         "rust", "kotlin", "ruby", "swift", "scala", "php")
LEVELS = ("Easy", "Medium", "Hard")
GOLDEN = "golden-baseline"                     # Compare.tsx: always the second model of a pair


def export_all(ex: Exporter) -> None:
    """Every request that the public pages send, as frontend/src mapped them
    (page by page, with the values that each control offers). A control that
    combines values freely (the ranking filters, the compare selections) gets the
    combinations listed here; any other combination shows "not in the snapshot"."""
    g = ex.get
    step = lambda name: print(f"- {name} ({len(ex.done)} requests, {ex.bytes / 1e6:.0f} MB so far)",
                              flush=True)

    step("lists")
    g("/api/version")
    g("/api/agents")
    g("/api/machines")
    g("/api/languages")
    g("/api/blog")
    g("/api/snapshots")
    models = [m["name"] for m in g("/api/models") or []]
    subs = g("/api/submissions") or []
    lists = {}
    for name in ("competitions", "registered-models", "harnesses", "correctness-oracles", "prompts"):
        g(f"/api/{name}")
        lists[name] = g(f"/api/{name}?include_retired=true") or []
    active = g("/api/competitions") or []
    C = [c["slug"] for c in active]
    H = [h["slug"] for h in g("/api/harnesses") or []]
    CO = [o["slug"] for o in g("/api/correctness-oracles") or []]
    P = [str(p["id"]) for p in g("/api/prompts") or []]
    agents = g("/api/agents") or []
    A = [a["slug"] for a in agents]
    K = sorted({a["kind"] for a in agents})

    step("problems")
    problems = g("/api/problems") or []
    for level in ("",) + LEVELS:
        for comp in [""] + C:
            q = [(k, v) for k, v in (("level", level), ("competition", comp)) if v]
            if q:
                g(f"/api/problems?{qs(q)}")
    seen = set()
    todo = [p["title_slug"] for p in problems]
    while todo:                                # a variant links to its original problem
        slug = todo.pop()
        if slug in seen:
            continue
        seen.add(slug)
        d = g(f"/api/problems/{slug}")
        if d and d.get("original_slug"):
            todo.append(d["original_slug"])

    step("catalog details")
    for name in models:
        g(f"/api/models/{seg(name)}")
    for slug in sorted({s["submitter_slug"] for s in subs if s.get("submitter_slug")}):
        g(f"/api/submitters/{seg(slug)}")
    for lang in g("/api/languages") or []:
        g(f"/api/languages/{seg(lang['name'])}")
    for h in lists["harnesses"] + [{"slug": "leetcode"}]:   # HarnessesHub links "leetcode"
        g(f"/api/harnesses/{seg(h['slug'])}")
    for o in lists["correctness-oracles"]:
        g(f"/api/correctness-oracles/{seg(o['slug'])}")
    for c in lists["competitions"]:
        d = g(f"/api/competitions/{seg(c['slug'])}") or {}
        for h in d.get("harnesses_with_data") or []:
            g(f"/api/competitions/{seg(c['slug'])}?{qs([('harness_slug', h)])}")
    for b in g("/api/blog") or []:
        g(f"/api/blog/{seg(b['slug'])}")
    for sn in g("/api/snapshots") or []:
        g(f"/api/snapshots/{sn['id']}")

    step("submissions")
    for s in subs:
        g(f"/api/submissions/{s['id']}")
        g(f"/api/submissions/{s['id']}/attempts")

    # The languages and harnesses that hold data in each competition: the ranking
    # filters return nothing for the others.
    langs_of = {c: sorted({s["language"] for s in subs if s.get("competition_slug") == c}
                          & set(LANGS)) for c in C}
    harn_of = {c["slug"]: [h for h in (c.get("harnesses_with_data") or []) if h in H] for c in active}

    step("home and efficiency rankings")
    for c in sorted(set(C) | {"leetcode-99-perf"}):
        g(f"/api/efficiency-ranks?{qs([('competition', c)])}")
    for c in C:
        for h in [""] + harn_of[c]:
            for lang in [""] + langs_of[c]:
                g("/api/efficiency-rankings?" + qs([(k, v) for k, v in (
                    ("competition", c), ("harness", h), ("language", lang)) if v]))
        # the other filters, one at a time
        for k, values in (("prompt_id", P), ("agent", A), ("agent_kind", K),
                          ("rounds", ("single", "multi"))):
            for v in values:
                g("/api/efficiency-rankings?" + qs([("competition", c), (k, v)]))

    step("submission rankings")
    for c in C:
        for h in [""] + harn_of[c]:
            for lang in [""] + langs_of[c]:
                for level in ("",) + LEVELS:
                    g("/api/rankings?" + qs([(k, v) for k, v in (
                        ("language", lang), ("level", level), ("competition", c),
                        ("harness", h)) if v]))
        for k, values in (("correctness_oracle", CO), ("prompt_id", P), ("agent", A),
                          ("agent_kind", K), ("rounds", ("single", "multi"))):
            for v in values:
                g("/api/rankings?" + qs([("competition", c), (k, v)]))
        rows = g("/api/rankings?" + qs([("competition", c)])) or []
        for n in sorted({r.get("n_samples_per_problem") for r in rows} - {None, 0}):
            g("/api/rankings?" + qs([("k", n), ("competition", c)]))

    step("compare: every pair of models, and head-to-head in both orders")
    in_comp = {c: {s["model_name"] for s in subs if s.get("competition_slug") == c} for c in C}
    for a, b in combinations(models, 2):
        g("/api/compare?" + qs([("models", a), ("models", b)]))
        for c in C:
            if not {a, b} <= in_comp[c]:
                continue                       # no shared cells: nothing to compare
            g("/api/compare?" + qs([("models", a), ("models", b), ("competition", c)]))
            for x, y in ((a, b), (b, a)):      # the pick order decides a and b
                if x == GOLDEN:
                    x, y = y, x
                g("/api/head-to-head?" + qs([("competition", c), ("a", x), ("b", y)]))

    # Every pair of submissions would be quadratic in their number (19,328 pairs and
    # 1.2 GB for 290 submissions). The snapshot keeps the comparisons of different
    # models on the same cells: the newest submission of each model in each
    # competition and language, paired with the others of that group.
    step("compare: pairs of submissions in one competition and language")
    newest: dict[tuple, int] = {}
    for s in subs:
        k = (s.get("competition_slug") or "", s["language"], s["model_name"])
        newest[k] = max(newest.get(k, 0), s["id"])
    groups: dict[tuple, list[int]] = {}
    for (c, lang, _), sid in newest.items():
        groups.setdefault((c, lang), []).append(sid)
    for ids in groups.values():
        for i, j in combinations(sorted(ids), 2):
            g("/api/compare/submissions?" + qs([("ids", i), ("ids", j)]))

    step("compare: every pair of prompts that a model used")
    used: dict[tuple[str, str], set[str]] = {}
    for s in subs:
        if s.get("prompt_id") is not None:
            used.setdefault((s["model_name"], s.get("model_version") or ""), set()).add(str(s["prompt_id"]))
    for rm in lists["registered-models"]:
        mv = (rm["name"], rm.get("version") or "")
        for p1, p2 in combinations(sorted(used.get(mv, set()) & set(P)), 2):
            for c in [""] + C:
                q = [("model_name", mv[0])] + ([("model_version", mv[1])] if mv[1] else [])
                q += [("prompt_ids", p1), ("prompt_ids", p2)] + ([("competition", c)] if c else [])
                g("/api/compare/prompts?" + qs(q))

    step("cost estimator: no filter, then each filter value alone")
    stats = g("/api/cost-estimator-stats") or {}
    for k, field, pick in (("prompt_id", "available_prompts", "id"),
                           ("language", "available_languages", None),
                           ("model_name", "available_models", None),
                           ("competition", "available_competitions", "slug")):
        for v in stats.get(field) or []:
            val = v.get(pick) if isinstance(v, dict) and pick else v
            if val not in (None, ""):
                g("/api/cost-estimator-stats?" + qs([(k, val)]))


if __name__ == "__main__":
    sys.exit(main())
