"""Typed-ish resource groups over ArenaClient. Each method maps one endpoint.
Where a newer endpoint may be missing on an older server, the method falls
back to the old way and says so in its docstring."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..errors import ArenaHTTPError
from .client import ArenaClient


def _route_missing(e: ArenaHTTPError) -> bool:
    """True when the server has no such route (an older deployment), as
    opposed to a 404 for a missing object."""
    return e.status in (404, 405) and ('"Not Found"' in e.body
                                       or '"Method Not Allowed"' in e.body)


class _Group:
    def __init__(self, client: ArenaClient):
        self.c = client


class Problems(_Group):
    def list(self, *, competition: str | None = None, level: str | None = None) -> list[dict]:
        params = {k: v for k, v in {"competition": competition, "level": level}.items() if v}
        return self.c.get("/api/problems", params=params, auth=False)

    def get(self, slug: str) -> dict:
        return self.c.get(f"/api/problems/{quote(slug)}", auth=False)


class Prompts(_Group):
    def list(self) -> list[dict]:
        return self.c.get("/api/prompts", auth=False)

    def get(self, name: str) -> dict:
        prompts = self.list()
        for p in prompts:
            if p.get("name") == name:
                return p
        names = ", ".join(p["name"] for p in prompts) or "(none)"
        raise KeyError(f"prompt {name!r} not in the registry; available: {names}")


class Competitions(_Group):
    def list(self) -> list[dict]:
        return self.c.get("/api/competitions", auth=False)

    def get(self, slug: str) -> dict:
        return self.c.get(f"/api/competitions/{quote(slug)}", auth=False)


class Submissions(_Group):
    def get(self, sid: int) -> dict:
        return self.c.get(f"/api/submissions/{sid}")

    def attempts(self, sid: int) -> list[dict]:
        return self.c.get(f"/api/submissions/{sid}/attempts")

    def mine(self) -> list[dict]:
        return self.c.get("/api/me/submissions")

    def create(self, body: dict) -> dict:
        return self.c.post("/api/submissions", body)

    def append(self, sid: int, attempts: list[dict]) -> dict:
        return self.c.post(f"/api/submissions/{sid}/attempts", {"attempts": attempts})

    def redispatch(self, sid: int) -> dict:
        """Admin: dispatch the submission's pending attempts again."""
        return self.c.post(f"/api/admin/submissions/{sid}/dispatch", {})

    def upsert(self, cell: dict) -> dict:
        """Find-or-create the caller's submission for an experiment cell.
        Returns {"submission_id", "created", "status"}; with cell["create"]
        false it only looks (submission_id None when nothing matches). On a
        server without PUT /api/submissions/upsert, matches client-side on
        /api/me/submissions (the scripts' find_existing_submission rule) and
        creates an empty submission when nothing matches."""
        try:
            return self.c.put("/api/submissions/upsert", cell)
        except ArenaHTTPError as e:
            if not _route_missing(e):
                raise
        match = find_matching_submission(self.mine(), cell)
        if match is not None:
            return {"submission_id": match["id"], "created": False,
                    "status": match.get("status")}
        if cell.get("create") is False:
            return {"submission_id": None, "created": False, "status": None}
        body = {k: v for k, v in cell.items() if k not in ("include_fully_accepted", "create")}
        out = self.create({**body, "attempts": []})
        return {"submission_id": out["submission_id"], "created": True,
                "status": out.get("status")}


def find_matching_submission(subs: list[dict], cell: dict) -> dict | None:
    """Client-side cell match, the rule of the scripts'
    find_existing_submission: newest first; legacy rows with no competition
    or harness match any."""
    hits = []
    for s in subs:
        if (s.get("model_name"), s.get("model_version"), s.get("language")) != (
                cell["model_name"], cell["model_version"], cell["language"]):
            continue
        if not cell.get("include_fully_accepted") and s.get("fully_accepted"):
            continue
        if (s.get("prompt_id") or None) != (cell.get("prompt_id") or None):
            continue
        if bool(s.get("include_hints")) != bool(cell.get("include_hints")):
            continue
        if (s.get("optimization_target") or None) != (cell.get("optimization_target") or None):
            continue
        if int(s.get("n_rounds") or 1) != int(cell.get("n_rounds") or 1):
            continue
        comp = cell.get("competition_slug") or "leetcode-99-perf"
        if s.get("competition_slug") not in (None, comp):
            continue
        if s.get("harness_slug") not in (None, cell.get("harness_slug")):
            continue
        hits.append(s)
    hits.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    return hits[0] if hits else None


class Attempts(_Group):
    def state(self, attempt_id: int) -> dict:
        """GET /api/attempts/{id}. Raises ArenaHTTPError on an older server
        that lacks the route (see resources._route_missing)."""
        return self.c.get(f"/api/attempts/{attempt_id}", auth=False)


class Oracles(_Group):
    def harnesses(self) -> list[dict]:
        return self.c.get("/api/harnesses")

    def correctness_oracles(self) -> list[dict]:
        return self.c.get("/api/correctness-oracles")

    def harness(self, slug: str) -> dict:
        return self.c.get(f"/api/harnesses/{quote(slug)}")

    def correctness_oracle(self, slug: str) -> dict:
        return self.c.get(f"/api/correctness-oracles/{quote(slug)}")


class Replicas(_Group):
    def list(self, oracle_slug: str, *, correctness: bool = False) -> list[dict]:
        kind = "correctness-oracles" if correctness else "harnesses"
        return self.c.get(f"/api/{kind}/{quote(oracle_slug)}/replicas")

    def add(self, *, name: str, endpoint: str, harness: str | None = None,
            correctness_oracle: str | None = None, **fields) -> dict:
        return self.c.post("/api/admin/replicas", {
            "name": name, "endpoint": endpoint, "harness_slug": harness,
            "correctness_oracle_slug": correctness_oracle, **fields})

    def edit(self, rid: int, **changes) -> dict:
        return self.c.put(f"/api/admin/replicas/{rid}", changes)

    def remove(self, rid: int) -> None:
        self.c.delete(f"/api/admin/replicas/{rid}")

    def probe(self, rid: int) -> dict:
        return self.c.post(f"/api/admin/replicas/{rid}/probe", {})

    def calibrate(self, rid: int, *, machine_class: str | None = None,
                  set_baseline: bool = False) -> dict:
        return self.c.post(f"/api/admin/replicas/{rid}/calibrate", {
            "machine_class_slug": machine_class, "set_baseline": set_baseline})

    def calibrations(self, rid: int) -> list[dict]:
        return self.c.get(f"/api/admin/replicas/{rid}/calibrations")

    def inconsistent(self) -> dict:
        return self.c.get("/api/admin/replicas/inconsistent")


class MachineClasses(_Group):
    def list(self) -> list[dict]:
        return self.c.get("/api/machine-classes", auth=False)

    def create(self, **fields) -> dict:
        return self.c.post("/api/admin/machine-classes", fields)

    def assign(self, machine_id: int, class_slug: str | None) -> dict:
        return self.c.put(f"/api/admin/machines/{machine_id}/class",
                          {"machine_class_slug": class_slug})


class Leaderboard(_Group):
    def rankings(self, **params: Any) -> Any:
        return self.c.get("/api/rankings", params=params, auth=False)

    def efficiency_ranks(self, **params: Any) -> Any:
        return self.c.get("/api/efficiency-ranks", params=params, auth=False)

    def compare(self, **params: Any) -> Any:
        return self.c.get("/api/compare", params=params, auth=False)

    def models(self) -> list[dict]:
        return self.c.get("/api/registered-models", auth=False)

    def pricing(self) -> list[dict]:
        """Per-model rates; [] on a server without GET /api/pricing."""
        try:
            return self.c.get("/api/pricing", auth=False)
        except ArenaHTTPError as e:
            if _route_missing(e):
                return []
            raise


class Admin(_Group):
    def me(self) -> dict:
        return self.c.get("/api/me")

    def queue(self) -> dict:
        return self.c.get("/api/admin/queue")

    def provenance_log(self, limit: int = 1) -> dict:
        return self.c.get("/api/provenance/log", params={"limit": limit}, auth=False)
