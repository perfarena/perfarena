"""From a model's raw answer to the code that is submitted.

CodePipeline is a chain of small steps (Chain of Responsibility): strip
<think> blocks, take the fenced block for the language, normalize for the
problem's harness. Separate helpers check the language fingerprint and read
model-chosen compile flags. The rules are the scripts' (parity-tested)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


def think_reasoning(content: str) -> str | None:
    """Reasoning carried inline as <think>...</think>, if any."""
    m = _THINK_RE.search(content or "")
    return m.group(1).strip() if m else None


def strip_think(content: str) -> str:
    return _THINK_RE.sub("", content or "")


def extract_code(text: str, fence_lang: str | None = None) -> str:
    """The first fenced block, preferring one tagged with the language;
    else any fenced block; else the whole text."""
    if fence_lang:
        m = re.search(rf"```{re.escape(fence_lang)}\s*\n(.*?)```", text,
                      re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1).strip()
    m = re.search(r"```[A-Za-z0-9_+-]*\s*\n(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip()


def normalize(code: str, language: str, is_clbg: bool = False) -> str:
    """Per-harness cleanup. PHP only: LeetCode wraps the code in an open
    <?php context (strip the tag), a CLBG program runs standalone (add it)."""
    if not code or language != "php":
        return code
    stripped = code.lstrip()
    if is_clbg:
        if not re.match(r"<\?(php\b|=)", stripped, re.IGNORECASE):
            code = "<?php\n" + code
        return code
    m = re.match(r"<\?php\s*(?:declare\s*\([^)]*\)\s*;\s*)?", stripped, re.IGNORECASE)
    if m:
        stripped = stripped[m.end():].lstrip()
    return re.sub(r"\s*\?>\s*$", "", stripped)


@dataclass
class CodeContext:
    content: str
    language: str
    fence: str
    is_clbg: bool
    code: str = ""


Step = Callable[[CodeContext], CodeContext]


def _strip_think_step(ctx: CodeContext) -> CodeContext:
    ctx.code = strip_think(ctx.content)
    return ctx


def _extract_step(ctx: CodeContext) -> CodeContext:
    ctx.code = extract_code(ctx.code, fence_lang=ctx.fence)
    return ctx


def _normalize_step(ctx: CodeContext) -> CodeContext:
    ctx.code = normalize(ctx.code, ctx.language, ctx.is_clbg)
    return ctx


class CodePipeline:
    """Ordered steps over a CodeContext. Add a step to handle a new
    harness quirk without touching the providers."""

    def __init__(self, steps: list[Step] | None = None):
        self.steps = steps or [_strip_think_step, _extract_step, _normalize_step]

    def run(self, content: str, *, language: str, fence: str, is_clbg: bool) -> str:
        ctx = CodeContext(content=content, language=language, fence=fence, is_clbg=is_clbg)
        for step in self.steps:
            ctx = step(ctx)
        return ctx.code


# ---------- language fingerprints ------------------------------------------------

LANGUAGE_FINGERPRINTS: dict[str, list[str]] = {
    "python3":    [r"^\s*def\s+\w+\s*\(", r"^\s*class\s+\w+\s*[:\(]",
                   r"^\s*from\s+\w+\s+import\b", r"->\s*[\w\[]",
                   r"^\s*print\s*\(", r"\bsys\.(?:argv|stdin|stdout)\b",
                   r"\brange\s*\(",
                   r"^\s*import\s+(?:sys|math|os|array|itertools|collections|functools|re|heapq|bisect|string|time|decimal|struct|io)\b"],
    "cpp":        [r"^\s*#include", r"\bvector\s*<", r"^\s*class\s+\w+\s*\{",
                   r"\bpublic\s*:", r"\bstd::"],
    "c":          [r"^\s*#include", r"\bint\s+main\s*\(",
                   r"\bstruct\s+\w+\s*\{", r"\bprintf\s*\(",
                   r"(?m)^\s*(?:int|char|bool|void|double|float|long|short|struct\s+\w+)\s*\*?\s*\*?\s*\w+\s*\(\s*(?:int|char|bool|void|double|float|long|short|struct\s+\w+)",
                   r"\bint\s*\*\*\s*\w+\b", r"\bint\s+\w+Size\b", r"\bstruct\s+\w+\s*\*"],
    "java":       [r"^\s*public\s+class\s+\w+",
                   r"\bpublic\s+[\w\[\]<>]+\s+\w+\s*\([^)]*\)\s*\{",
                   r"\bSystem\.out\.", r"\bimport\s+java\.",
                   r"\bnew\s+\w+\s*\[",
                   r"\b(?:int|long|double|float|boolean|char|String)\s*\[\]"],
    "csharp":     [r"\bpublic\s+class\s+\w+", r"\busing\s+System\b",
                   r"\bConsole\.\w+\("],
    "javascript": [r"^\s*function\s+\w+\s*\(", r"\bvar\s+\w+\s*=",
                   r"\blet\s+\w+\s*=", r"\bconst\s+\w+\s*=", r"=>"],
    "typescript": [r":\s*(string|number|boolean|void)\b", r"\binterface\s+\w+",
                   r"^\s*function\s+\w+\s*\(.*:.*\)\s*:",
                   r"^\s*function\s+\w+\s*\(", r"\bvar\s+\w+\s*=",
                   r"\blet\s+\w+\s*=", r"\bconst\s+\w+\s*=", r"=>"],
    "golang":     [r"^\s*package\s+\w+", r"^\s*func\s+\w+\s*\(", r"^\s*import\s+\("],
    "kotlin":     [r"^\s*fun\s+\w+\s*\(", r"\bval\s+\w+\s*[=:]",
                   r"\bvar\s+\w+\s*[=:]", r"\bclass\s+\w+\s*\("],
    "swift":      [r"^\s*func\s+\w+\s*\(", r"^\s*class\s+\w+\s*:",
                   r"\blet\s+\w+\s*[:=]", r"\bguard\s+let\b"],
    "scala":      [r"^\s*object\s+\w+\b", r"^\s*def\s+\w+\s*\(.*\)\s*:",
                   r"\bval\s+\w+\s*[:=]"],
    "ruby":       [r"^\s*def\s+\w+", r"^\s*class\s+\w+\b", r"\bend\b\s*$", r"\bputs\s+"],
    "php":        [r"<\?php\b", r"\bfunction\s+\w+\s*\(", r"\$\w+"],
    "rust":       [r"^\s*fn\s+\w+\s*\(", r"\blet\s+(mut\s+)?\w+",
                   r"\bimpl\s+\w+", r"\buse\s+std::"],
    "dart":       [r"import\s+['\"]dart:",
                   r"\bvoid\s+main\s*\(\s*List<String>",
                   r"(?m)^\s*(?:int|double|bool|String|void|num)\s+\w+\s*\([^)]*\)\s*(?:\{|=>)",
                   r"\bList<[\w<>?,\s]+>\s+\w+",
                   r"\b\w+\?\s+\w+\s*[=,);]",
                   r"=>"],
    "elixir":     [r"^\s*defmodule\s+", r"(?m)^\s*defp?\s+\w+.*\bdo(\s|$)",
                   r"\|>", r"@spec\b"],
    "erlang":     [r"(?m)^\s*-module\(", r"(?m)^\s*-export\(", r"(?m)^\s*-spec\s",
                   r"(?m)^\s*-include\(", r"(?m)^[a-z][a-zA-Z0-9_]*\([^)]*\)\s*->"],
    "racket":     [r"#lang\s+racket", r"\(define(/contract)?\s+\(", r"\(require\b",
                   r"\(struct\b", r"\(class\s+object%", r"\(provide\b"],
}


def looks_like(code: str, language: str) -> bool:
    """Does the code plausibly match the language? True for a language with
    no fingerprints. A False result marks the attempt
    `wrong_language_extracted` (recorded as failed, raw answer kept)."""
    pats = LANGUAGE_FINGERPRINTS.get(language)
    if not pats:
        return True
    return any(re.search(p, code, re.MULTILINE) for p in pats)


# ---------- compile flags ----------------------------------------------------------

_FLAGS_MARKER_RE = re.compile(r"^[ \t>*-]*PERFARENA-FLAGS:[ \t]*(.+?)[ \t]*$",
                              re.IGNORECASE | re.MULTILINE)
_FLAG_TOKEN_RE = re.compile(r"^[A-Za-z0-9_+=./:-]+$")


def extract_compile_flags(text: str) -> str:
    """Flags from the LAST `PERFARENA-FLAGS:` marker line, or ""."""
    hits = _FLAGS_MARKER_RE.findall(text or "")
    return hits[-1].strip() if hits else ""


def sanitize_compile_flags(flags: str) -> str:
    """Keep only token-shaped flags (no shell metacharacters)."""
    return " ".join(t for t in (flags or "").split() if _FLAG_TOKEN_RE.match(t))
