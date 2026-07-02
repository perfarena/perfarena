"""Anti-contamination variant problems: dispatch substitutes function name
+ redirects to the original's slug; the variant's own metadata stays
distinct in the catalog so a model can't pattern-match its way to a
memorised solution.

See app/rewrite_competition.py for the CSV round-trip that produces
variants in bulk."""
from sqlmodel import Session, select

from app.db import engine
from app.dispatch import _resolve_dispatch_target
from app.models import Problem
from app.rewrite_competition import extract_function_name, substitute_function_name


# ---------- function-name extraction (pure helper) -------------------------

def test_extract_function_name_python():
    snip = {"python3": "class Solution:\n    def twoSum(self, nums, target):\n        pass\n"}
    assert extract_function_name(snip) == "twoSum"


def test_extract_function_name_java():
    snip = {"java": (
        "class Solution {\n"
        "    public int[] twoSum(int[] nums, int target) {\n"
        "        return null;\n"
        "    }\n"
        "}\n")}
    assert extract_function_name(snip) == "twoSum"


def test_extract_function_name_none_when_no_match():
    assert extract_function_name({"random": "// nothing"}) is None
    assert extract_function_name(None) is None
    assert extract_function_name({}) is None


def test_substitute_function_name_all_languages():
    snips = {
        "python3": "def twoSum(self, nums, target):",
        "java": "public int[] twoSum(int[] nums, int target) {",
        "cpp": "vector<int> twoSum(vector<int>& nums, int target) {",
    }
    out = substitute_function_name(snips, "twoSum", "findPair")
    assert "findPair" in out["python3"]
    assert "twoSum" not in out["python3"]
    assert "findPair" in out["java"]
    assert "findPair" in out["cpp"]


def test_substitute_function_name_noop_when_unchanged():
    snips = {"python3": "def x(): pass"}
    assert substitute_function_name(snips, "x", "x") == snips


def test_resolve_dispatch_canonical_problem_is_noop(client):
    """Originals (no original_problem_id) flow through unchanged."""
    with Session(engine) as s:
        original = s.exec(select(Problem)).first()
        slug, code = _resolve_dispatch_target(
            original, "class Solution:\n    pass", s,
        )
        assert slug == original.title_slug
        assert code == "class Solution:\n    pass"


def test_resolve_dispatch_variant_redirects_and_rewrites(client):
    """A variant routes to the ORIGINAL slug AND rewrites the function
    name back to what LC's judge expects."""
    with Session(engine) as s:
        original = s.exec(select(Problem)).first()
        variant = Problem(
            external_id=original.external_id,
            title_slug=f"rw-{original.title_slug}",
            title=f"Rewritten {original.title}",
            level=original.level,
            tags=original.tags,
            content="rephrased statement",
            code_snippets={"python3": "class Solution:\n    def findPair(...)"},
            original_problem_id=original.id,
            function_name="findPair",
            original_function_name="twoSum",
        )
        s.add(variant); s.commit(); s.refresh(variant)

        llm_code = (
            "class Solution:\n"
            "    def findPair(self, nums, target):\n"
            "        return [0, 1]\n"
        )
        slug, code = _resolve_dispatch_target(variant, llm_code, s)
        assert slug == original.title_slug
        assert "findPair" not in code
        assert "twoSum" in code


def test_resolve_dispatch_variant_with_dangling_original(client):
    """If the variant's original is missing, fall back to variant's own
    slug rather than blindly stripping function names."""
    with Session(engine) as s:
        v = Problem(
            external_id=9_999_999,
            title_slug="rw-orphan-test",
            title="Orphan Variant",
            level="Easy",
            tags="",
            content="",
            code_snippets={},
            original_problem_id=10_000_001,
            function_name="findPair",
            original_function_name="twoSum",
        )
        s.add(v); s.commit(); s.refresh(v)
        slug, code = _resolve_dispatch_target(v, "class Solution: pass", s)
        assert slug == v.title_slug
        assert code == "class Solution: pass"
