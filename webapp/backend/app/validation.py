"""Submit-time sanity checks on attempt code.

The judge decides *correctness*; this layer only rejects input that is
obviously not a real attempt — empty/trivial code, the documented placeholder,
or a stub whose function bodies are just `...` / `pass` / `raise
NotImplementedError`. It is intentionally conservative: it must never reject a
genuine solution, so it errs toward accepting anything plausible.
"""
from __future__ import annotations

import ast
import re

MIN_SUBSTANTIVE_CHARS = 40

_PLACEHOLDER_MARKERS = (
    "your code here",
    "todo",
    "fixme",
    "implement me",
    "not implemented",
    "notimplementederror",
    "unsupportedoperationexception",
)

# The snippet shipped in the docs / sample payload. Normalised (whitespace
# collapsed) so formatting variants still match.
_DOC_SAMPLE = "class solution: def twosum(self, nums, target): ..."


def _normalize(code: str) -> str:
    return re.sub(r"\s+", " ", code.strip().lower())


def _strip_comments(code: str) -> str:
    out = re.sub(r"#.*", "", code)            # python / shell
    out = re.sub(r"//.*", "", out)            # c-family line comments
    out = re.sub(r"/\*.*?\*/", "", out, flags=re.DOTALL)  # c-family block
    return out


def _is_python(language: str) -> bool:
    return "python" in language.lower()


def _is_stub_body(body: list[ast.stmt]) -> bool:
    """A function/method body that does nothing real: only a docstring,
    `pass`, a bare `...`, or `raise NotImplementedError`."""
    stmts = list(body)
    if stmts and isinstance(stmts[0], ast.Expr) and isinstance(
        getattr(stmts[0], "value", None), ast.Constant
    ) and isinstance(stmts[0].value.value, str):
        stmts = stmts[1:]  # drop leading docstring
    if not stmts:
        return True
    if len(stmts) != 1:
        return False
    s = stmts[0]
    if isinstance(s, ast.Pass):
        return True
    if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and (
        s.value.value is Ellipsis
    ):
        return True
    if isinstance(s, ast.Raise):
        exc = s.exc
        name = None
        if isinstance(exc, ast.Name):
            name = exc.id
        elif isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
            name = exc.func.id
        if name == "NotImplementedError":
            return True
    return False


def _validate_python(code: str) -> str | None:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"code does not parse as Python ({e.msg} at line {e.lineno})"
    funcs = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if not funcs:
        return "no function defined — looks like a placeholder, not a solution"
    if all(_is_stub_body(f.body) for f in funcs):
        return ("every function body is empty (`...`/`pass`/NotImplementedError) "
                "— stub/placeholder code, not a real solution")
    return None


def validate_code(language: str, code: str) -> str | None:
    """Return a human-readable rejection reason, or None if the code is
    plausibly a real attempt."""
    if not code or not code.strip():
        return "empty code"
    if _normalize(code) == _DOC_SAMPLE:
        return "this is the documentation placeholder, not a real solution"

    stripped = _strip_comments(code)
    substantive = re.sub(r"\s+", "", stripped)
    if len(substantive) < MIN_SUBSTANTIVE_CHARS:
        return f"code is too short ({len(substantive)} chars) to be a real solution"

    low = stripped.lower()
    for marker in _PLACEHOLDER_MARKERS:
        if marker in low:
            return f"code contains a placeholder marker ({marker!r})"

    if _is_python(language):
        return _validate_python(code)

    # Non-Python: no parser here, so just reject bodies that are only `...`.
    if re.search(r"\{\s*(\.\.\.)?\s*\}", stripped) and len(substantive) < 80:
        return "function body is empty — stub/placeholder code"
    return None
