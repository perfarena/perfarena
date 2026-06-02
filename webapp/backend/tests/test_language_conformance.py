"""Cross-language smuggling guard.

What this protects against: a submitter (or, more commonly, a small model
behind a submitter) ignores the language directive and emits code in a
language different from the one declared on the submission. LeetCode
silently re-classifies the submission by content-sniffing the code, returns
the verdict against THAT language's baseline (e.g. Java instead of Python),
and the misclassified row would contaminate cross-language aggregates if
left as-is. The incident that motivated this — a Java-syntaxed solution
shipped under lang=python3 returning 0 ms with 100th percentile — is
documented in app/dispatch.py's language-mismatch handler.

Two test surfaces:

  1. Server-side override: when the judge's `lang` field disagrees with the
     submission's declared `language`, dispatch overrides status_msg to
     "Language mismatch" before the Run is saved. The fake judge respects a
     `## fake_lang: <name>` marker so we can trigger this path without
     touching LeetCode.

  2. Client-side fingerprint: submit_with_local_model._looks_like(code,
     language) catches the same failure mode earlier — before the wrong-
     language code is even POSTed to the submit endpoint.
"""
import sys
from pathlib import Path

from conftest import ADMIN, GOOD_CODE, TRACE


def test_language_mismatch_marks_run(client):
    """The fake judge reports lang=java for code containing the test marker;
    dispatch flips status_msg to 'Language mismatch' and the attempt does NOT
    count as accepted toward pass@k."""
    body = {
        "model_name": "lang-cheat", "model_version": "v1",
        "language": "python3", "prompt_template": "stand-in prompt 1234567890",
        "model_params": {"temperature": 0.0},
        "attempts": [{
            "title_slug": "two-sum",
            "code": GOOD_CODE + "\n## fake_lang: java\n",   # triggers the hook
            "inference_trace": TRACE,
        }],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 202
    sid = r.json()["submission_id"]

    atts = client.get(f"/api/submissions/{sid}/attempts").json()
    assert len(atts) == 1
    a = atts[0]

    # Every dispatched run for this attempt should carry the override.
    assert a["n_runs"] >= 1
    statuses = {r["status_msg"] for r in a.get("runs", [])}
    assert statuses == {"Language mismatch"}, (
        f"expected every run to be flipped to Language mismatch; got {statuses}")

    # The attempt's aggregate must NOT count this as accepted.
    assert a["status"] != "Accepted"
    assert a["accepted"] is False
    assert a["n_accepted_runs"] == 0


def test_matching_language_passes_through(client):
    """Sanity check: code WITHOUT the fake_lang marker (so lang defaults to
    the submission's language) flows through unimpacted — the override only
    fires on a real mismatch, not on every run."""
    body = {
        "model_name": "lang-clean", "model_version": "v1",
        "language": "python3", "prompt_template": "stand-in prompt 1234567890",
        "model_params": {"temperature": 0.0},
        "attempts": [{
            "title_slug": "two-sum",
            "code": GOOD_CODE,   # no fake_lang marker
            "inference_trace": TRACE,
        }],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 202
    sid = r.json()["submission_id"]

    atts = client.get(f"/api/submissions/{sid}/attempts").json()
    statuses = {r["status_msg"] for r in atts[0].get("runs", [])}
    assert "Language mismatch" not in statuses, (
        f"override fired on a same-language run: {statuses}")


# ---------- client-side _looks_like ---------------------------------------

# Import the heuristic from the local-model submitter. It lives in
# webapp/examples/, so a small sys.path nudge is needed for tests/ to find it.
EXAMPLES = Path(__file__).parent.parent.parent / "examples"
sys.path.insert(0, str(EXAMPLES))
from submit_with_local_model import _looks_like   # noqa: E402


_PY_OK = (
    "class Solution:\n"
    "    def twoSum(self, nums, target):\n"
    "        seen = {}\n"
    "        for i, x in enumerate(nums):\n"
    "            if target - x in seen: return [seen[target - x], i]\n"
    "            seen[x] = i\n"
)

_JAVA_OK = (
    "class Solution {\n"
    "    public int[] twoSum(int[] nums, int target) {\n"
    "        return new int[]{0, 1};\n"
    "    }\n"
    "}\n"
)

_CPP_OK = (
    "#include <vector>\n"
    "class Solution {\n"
    "public:\n"
    "    std::vector<int> twoSum(std::vector<int>& nums, int target) {\n"
    "        return {0, 1};\n"
    "    }\n"
    "};\n"
)


def test_looks_like_detects_obvious_cases():
    assert _looks_like(_PY_OK, "python3") is True
    assert _looks_like(_JAVA_OK, "java") is True
    assert _looks_like(_CPP_OK, "cpp") is True


def test_looks_like_catches_wrong_language():
    # The actual incident — Java code submitted under lang=python3.
    assert _looks_like(_JAVA_OK, "python3") is False
    # And the symmetric: Python code submitted under lang=java should also fail.
    assert _looks_like(_PY_OK, "java") is False
    # C++ code under python3 — `vector<` is C++ syntax, not Python.
    assert _looks_like(_CPP_OK, "python3") is False


def test_looks_like_permissive_for_unknown_languages():
    """Languages without a fingerprint list pass through (don't fail-open
    on a code shape we have no opinion about — that'd block legitimate
    submissions in languages we just haven't tabulated yet)."""
    assert _looks_like(_PY_OK, "haskell") is True
    assert _looks_like(_JAVA_OK, "ocaml") is True


def test_looks_like_handles_empty_code():
    # _extract_code falls back to the whole text trimmed if no fence is
    # found; the result can be empty for a completely garbled model output.
    assert _looks_like("", "python3") is False
    assert _looks_like("\n\n   \n", "java") is False
