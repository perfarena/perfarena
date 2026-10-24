"""Language slugs the platform accepts: display name and markdown fence tag.
Mirrors webapp/backend/app/languages.py and the client scripts."""
from __future__ import annotations

LANGUAGE_DISPLAY: dict[str, tuple[str, str]] = {
    "python3":    ("Python 3",   "python"),
    "cpp":        ("C++",        "cpp"),
    "c":          ("C",          "c"),
    "rust":       ("Rust",       "rust"),
    "java":       ("Java",       "java"),
    "csharp":     ("C#",         "csharp"),
    "javascript": ("JavaScript", "javascript"),
    "typescript": ("TypeScript", "typescript"),
    "golang":     ("Go",         "go"),
    "kotlin":     ("Kotlin",     "kotlin"),
    "swift":      ("Swift",      "swift"),
    "scala":      ("Scala",      "scala"),
    "ruby":       ("Ruby",       "ruby"),
    "php":        ("PHP",        "php"),
    "dart":       ("Dart",       "dart"),
    "elixir":     ("Elixir",     "elixir"),
    "erlang":     ("Erlang",     "erlang"),
    "racket":     ("Racket",     "racket"),
}

# The languages the arena-local harness can validate and measure (the
# runner's perfarena_handler._LANG_MAP). Today that is every language above.
MEASURABLE_LANGUAGES: tuple[str, ...] = tuple(LANGUAGE_DISPLAY)


def display(language: str) -> str:
    return LANGUAGE_DISPLAY.get(language, (language, language))[0]


def fence(language: str) -> str:
    return LANGUAGE_DISPLAY.get(language, (language, language))[1]
