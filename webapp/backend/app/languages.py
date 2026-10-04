"""Static language metadata + DB-derived stats for the per-language pages.

Survey numbers are approximations from publicly available developer surveys
(Stack Overflow 2024, TIOBE 2025). They're snapshotted here to keep the page
reasonable to render; refresh them when newer data is available.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class LanguageInfo:
    name: str
    display_name: str
    category: str
    paradigms: list[str] = field(default_factory=list)
    typing: str = ""
    gc: str = ""
    runtime: str = ""
    design_notes: str = ""
    key_traits: list[str] = field(default_factory=list)
    # Public-survey approximations
    so_used_pct_2024: float | None = None
    so_admired_pct_2024: float | None = None
    tiobe_rank_2025: int | None = None
    # Qualitative performance footprint
    cold_start: str = ""
    peak_speed: str = ""
    memory_baseline: str = ""


LANGUAGES: dict[str, LanguageInfo] = {
    "python3": LanguageInfo(
        name="python3", display_name="Python 3",
        category="Interpreted (bytecode + dispatch loop)",
        paradigms=["Multi-paradigm", "Object-oriented", "Functional"],
        typing="Dynamically and strongly typed; gradual via PEP 484 type hints",
        gc="Reference counting + cyclic collector",
        runtime="CPython VM (typical); also PyPy (tracing JIT), GraalPy",
        design_notes=(
            "CPython evaluates compiled bytecode in a per-instruction dispatch loop. "
            "Each value is a heap-allocated PyObject with attached type and refcount "
            "metadata, so even simple arithmetic chases pointers. The GIL serializes "
            "Python-level execution within one process, but releases for I/O and many "
            "C extensions. The language wins on developer velocity and library breadth, "
            "not on tight inner loops."
        ),
        key_traits=[
            "High per-operation overhead in the interpreter dispatch loop",
            "Heap-heavy: every int, list slot, and string is a PyObject",
            "GIL-bound for CPU work in the default runtime",
            "Excellent for I/O-bound and glue code; C extensions for hot paths",
        ],
        so_used_pct_2024=51.0, so_admired_pct_2024=67.0, tiobe_rank_2025=1,
        cold_start="Fast (interpreter starts in ~30 ms)",
        peak_speed="~50–200× slower than C++ on arithmetic loops",
        memory_baseline="High — PyObject headers dominate small data",
    ),
    "cpp": LanguageInfo(
        name="cpp", display_name="C++",
        category="Compiled to native machine code",
        paradigms=["Multi-paradigm", "Generic", "Object-oriented", "Procedural"],
        typing="Statically and strongly typed; templates expand at compile time",
        gc="None — manual + RAII; smart pointers (unique_ptr, shared_ptr)",
        runtime="No runtime — produces a standalone executable",
        design_notes=(
            "C++ targets zero-cost abstractions: templates monomorphize, inline "
            "expansion is aggressive, and ownership is encoded in types rather than "
            "policed by a runtime collector. The trade is build complexity and the "
            "responsibility to avoid UB. For LeetCode-style problems C++ usually sets "
            "the ceiling for runtime + memory."
        ),
        key_traits=[
            "Predictable, near-optimal codegen with -O2/-O3",
            "No GC pauses; deterministic destruction",
            "Templates → monomorphization → no virtual dispatch overhead",
            "Memory footprint is whatever you allocate; nothing else",
        ],
        so_used_pct_2024=23.0, so_admired_pct_2024=47.0, tiobe_rank_2025=2,
        cold_start="Instant — no runtime to boot",
        peak_speed="State of the art alongside C and Rust",
        memory_baseline="Minimal — only your data + the standard library",
    ),
    "c": LanguageInfo(
        name="c", display_name="C",
        category="Compiled to native machine code",
        paradigms=["Procedural"],
        typing="Statically typed, weak (implicit conversions)",
        gc="None — manual",
        runtime="Tiny libc; otherwise none",
        design_notes=(
            "C is the canonical low-level language. The compiled output is close to "
            "the source: function calls, loads, stores. No abstraction layer to push "
            "through. The cost is verbosity and a small standard library."
        ),
        key_traits=[
            "Smallest binaries and lowest memory of mainstream languages",
            "No abstraction overhead at all",
            "Manual memory management is a real source of bugs",
        ],
        so_used_pct_2024=20.5, so_admired_pct_2024=47.0, tiobe_rank_2025=3,
        cold_start="Instant",
        peak_speed="Reference for native-code performance",
        memory_baseline="Minimal",
    ),
    "rust": LanguageInfo(
        name="rust", display_name="Rust",
        category="Compiled to native machine code",
        paradigms=["Multi-paradigm", "Functional-leaning", "Systems"],
        typing="Statically typed with full inference; trait-based generics; ownership in the type system",
        gc="None — ownership + borrow checking enforced at compile time",
        runtime="No runtime — produces a standalone executable",
        design_notes=(
            "Rust matches C++ on speed without needing manual memory hygiene: the "
            "borrow checker proves at compile time that references don't outlive "
            "their owners and that aliasing rules are respected. Generics monomorphize, "
            "so abstractions compile down to the same machine code you'd write by hand."
        ),
        key_traits=[
            "C/C++ class performance with memory + thread safety guarantees",
            "No GC, no runtime — predictable latency",
            "Iterator chains optimize to tight loops",
            "Steeper learning curve than most languages on this list",
        ],
        so_used_pct_2024=12.6, so_admired_pct_2024=82.5, tiobe_rank_2025=14,
        cold_start="Instant",
        peak_speed="Within ~1.0–1.1× of C++; sometimes faster on safe-by-default code",
        memory_baseline="Minimal",
    ),
    "java": LanguageInfo(
        name="java", display_name="Java",
        category="Managed — JVM bytecode + JIT to native",
        paradigms=["Object-oriented", "Generic (erased)"],
        typing="Statically and strongly typed; generics via type erasure",
        gc="Yes — multiple collectors (G1, ZGC, Parallel)",
        runtime="JVM (HotSpot/OpenJ9/GraalVM); tiered JIT compilation",
        design_notes=(
            "Java compiles to JVM bytecode that the JIT compiles to native code as hot "
            "paths emerge. Steady-state throughput is competitive with C++ on many "
            "workloads, but startup pays a fixed cost and the JVM holds a multi-MB "
            "memory baseline. The platform is exceptionally mature: profilers, "
            "monitoring, and deterministic GC tuning are first-class."
        ),
        key_traits=[
            "JIT bridges interpreter ergonomics with near-native peak speed",
            "JVM startup latency + memory baseline (~30–100 MB)",
            "GC pauses are tunable but exist",
            "Reference types incur indirection vs. value-type langs",
        ],
        so_used_pct_2024=30.3, so_admired_pct_2024=44.0, tiobe_rank_2025=4,
        cold_start="Slow — JVM warmup; hundreds of ms",
        peak_speed="Within 1–2× of C++ in steady state",
        memory_baseline="Multi-MB JVM heap + metaspace before your code",
    ),
    "csharp": LanguageInfo(
        name="csharp", display_name="C#",
        category="Managed — CIL bytecode + RyuJIT to native",
        paradigms=["Object-oriented", "Functional", "Generic (reified)"],
        typing="Statically and strongly typed; generics are reified",
        gc="Yes — generational",
        runtime=".NET CoreCLR (RyuJIT) or Mono",
        design_notes=(
            "C# runs on the CLR with reified generics (unlike Java's erased ones), "
            "value types (structs), Span<T> for zero-copy memory, and tiered JIT. The "
            "language has steadily added expressive constructs (records, pattern "
            "matching) without paying a runtime cost. Performance is comparable to Java "
            "with somewhat lower memory ceilings."
        ),
        key_traits=[
            "Reified generics avoid boxing in many cases",
            "Value types and Span<T> reduce allocation pressure",
            "JIT warmup similar to Java but ReadyToRun shortens cold start",
            "GC and JIT add overhead vs. native code",
        ],
        so_used_pct_2024=27.1, so_admired_pct_2024=60.0, tiobe_rank_2025=5,
        cold_start="Moderate — faster than JVM with ReadyToRun",
        peak_speed="Within 1.2–2× of C++",
        memory_baseline="Lower than JVM but still managed-runtime overhead",
    ),
    "javascript": LanguageInfo(
        name="javascript", display_name="JavaScript",
        category="Managed — V8/SpiderMonkey JIT (single-threaded event loop)",
        paradigms=["Multi-paradigm", "Functional", "Prototype-based OO"],
        typing="Dynamically and weakly typed",
        gc="Yes — generational with incremental marking",
        runtime="V8 (Node, Chromium), SpiderMonkey (Firefox), JavaScriptCore (Safari)",
        design_notes=(
            "Modern JS engines are some of the most aggressive JITs in production. V8 "
            "speculates on observed types; if hidden classes stay stable, hot functions "
            "compile to fast native code. Performance can drop sharply when "
            "polymorphic shapes break those speculations. Single-threaded by default; "
            "Workers exist but don't share memory by default."
        ),
        key_traits=[
            "Highly optimized JIT — fast for steady-state numeric loops",
            "Sensitive to object shape changes (hidden class transitions)",
            "Single event loop — CPU work blocks I/O",
            "Memory layout less predictable than statically-typed peers",
        ],
        so_used_pct_2024=62.3, so_admired_pct_2024=60.0, tiobe_rank_2025=6,
        cold_start="Fast (Node ~30–80 ms)",
        peak_speed="Within 1.5–3× of C++ on numerically clean code",
        memory_baseline="Moderate — V8 runtime + GC state",
    ),
    "typescript": LanguageInfo(
        name="typescript", display_name="TypeScript",
        category="Transpiled to JavaScript — same runtime characteristics",
        paradigms=["Multi-paradigm"],
        typing="Statically and structurally typed at compile time; runtime is JS",
        gc="Yes — inherited from JS runtime",
        runtime="Whatever runs the emitted JS (Node, browsers, Deno, Bun)",
        design_notes=(
            "TypeScript adds a strong structural type system on top of JavaScript. "
            "Types are checked at compile time and erased at runtime, so runtime "
            "performance and memory characteristics match plain JS. Newer runtimes "
            "(Bun, Deno) and tools like SWC/esbuild make the build step nearly free."
        ),
        key_traits=[
            "Identical runtime profile to JavaScript",
            "Compile-time type guarantees catch a class of bugs",
            "No runtime overhead from the type system",
        ],
        so_used_pct_2024=38.5, so_admired_pct_2024=67.0, tiobe_rank_2025=10,
        cold_start="Same as JS",
        peak_speed="Same as JS",
        memory_baseline="Same as JS",
    ),
    "golang": LanguageInfo(
        name="golang", display_name="Go",
        category="Compiled to native — small runtime + goroutine scheduler",
        paradigms=["Procedural", "Imperative", "Concurrency-first"],
        typing="Statically and strongly typed; generics since Go 1.18",
        gc="Yes — concurrent, sub-ms pauses",
        runtime="Tiny runtime baked into the binary (scheduler + GC + reflect)",
        design_notes=(
            "Go compiles ahead-of-time with no JIT warmup and ships a small runtime "
            "that supplies a cooperative goroutine scheduler and a low-pause GC. "
            "Performance is in the same order of magnitude as Java/C# but cold start "
            "is essentially instant. Memory usage is modest."
        ),
        key_traits=[
            "AOT compiled — no JIT warmup",
            "Sub-millisecond GC pauses suit latency-sensitive code",
            "Goroutines + channels give cheap concurrency",
            "Less aggressive optimization than JVM JITs on tight numerical loops",
        ],
        so_used_pct_2024=13.5, so_admired_pct_2024=60.0, tiobe_rank_2025=7,
        cold_start="Instant",
        peak_speed="Within 1.5–3× of C++ on most workloads",
        memory_baseline="Low — small runtime overhead",
    ),
    "kotlin": LanguageInfo(
        name="kotlin", display_name="Kotlin",
        category="Managed — JVM bytecode (also Native, JS, Wasm)",
        paradigms=["Multi-paradigm", "Functional-leaning", "Object-oriented"],
        typing="Statically and strongly typed; pervasive type inference; nullability in the type system",
        gc="Yes — inherited from the JVM target",
        runtime="JVM (most common); Kotlin/Native via LLVM",
        design_notes=(
            "Kotlin on the JVM compiles to bytecode and inherits HotSpot's JIT, so "
            "steady-state performance tracks Java closely. Inline functions and "
            "value classes can avoid boxing. Kotlin/Native is a separate AOT backend "
            "for environments where the JVM isn't available."
        ),
        key_traits=[
            "Performance parity with Java in steady state",
            "Inline lambdas + value classes reduce allocation pressure",
            "Nullability checks are compile-time only (no runtime cost)",
        ],
        so_used_pct_2024=9.4, so_admired_pct_2024=60.0, tiobe_rank_2025=18,
        cold_start="Same JVM warmup as Java",
        peak_speed="On par with Java",
        memory_baseline="Same JVM baseline",
    ),
    "swift": LanguageInfo(
        name="swift", display_name="Swift",
        category="Compiled to native via LLVM — ARC memory model",
        paradigms=["Multi-paradigm", "Protocol-oriented", "Functional", "Object-oriented"],
        typing="Statically and strongly typed; protocols with associated types",
        gc="ARC (Automatic Reference Counting) — deterministic, no tracing GC",
        runtime="Small Swift runtime; system frameworks on Apple platforms",
        design_notes=(
            "Swift compiles ahead-of-time via LLVM and uses ARC instead of a tracing "
            "collector, giving deterministic deallocation and no GC pauses. Value "
            "types (structs) are first-class, and the optimizer monomorphizes "
            "protocols when possible. Performance is between Go and C++ depending on "
            "how much dynamic dispatch survives optimization."
        ),
        key_traits=[
            "No GC pauses — ARC is deterministic",
            "Value semantics for many built-in types",
            "AOT compiled, fast startup",
            "Reference cycles require manual breakage (weak/unowned)",
        ],
        so_used_pct_2024=4.7, so_admired_pct_2024=56.0, tiobe_rank_2025=15,
        cold_start="Instant",
        peak_speed="Within 1.2–2× of C++",
        memory_baseline="Low",
    ),
    "dart": LanguageInfo(
        name="dart", display_name="Dart",
        category="AOT native (dart compile exe) or JIT VM — tracing GC",
        paradigms=["Multi-paradigm", "Object-oriented", "Functional"],
        typing="Statically and soundly typed; sound null safety",
        gc="Yes — generational tracing collector",
        runtime="Dart VM (JIT) for development; AOT native executable for production",
        design_notes=(
            "Dart runs two ways from one toolchain. `dart run` executes on a "
            "JIT VM with fast startup; `dart compile exe` builds an "
            "ahead-of-time native executable with no VM warmup. Both use a "
            "generational tracing garbage collector. Small integers box on the "
            "VM, so arithmetic-heavy loops trail the native and JVM languages, "
            "but AOT startup is instant and the memory profile is moderate."
        ),
        key_traits=[
            "One toolchain, two runtimes: JIT VM and AOT native",
            "Generational tracing GC — brief pauses under allocation churn",
            "Sound null-safe static typing",
            "Between Go and the JVM on arithmetic-heavy loops",
        ],
        so_used_pct_2024=6.0, so_admired_pct_2024=62.0, tiobe_rank_2025=25,
        cold_start="AOT: instant; JIT VM: fast",
        peak_speed="AOT within ~1.5–3× of C++ on arithmetic",
        memory_baseline="Moderate — VM/GC heap overhead",
    ),
    "scala": LanguageInfo(
        name="scala", display_name="Scala",
        category="Managed — JVM bytecode (also Scala Native, Scala.js)",
        paradigms=["Multi-paradigm", "Functional", "Object-oriented"],
        typing="Statically and strongly typed; advanced type system (implicits, higher-kinded types)",
        gc="Yes — inherited from JVM target",
        runtime="JVM (HotSpot/GraalVM)",
        design_notes=(
            "Scala targets the JVM and benefits from the same JIT, but functional "
            "patterns (boxing of value types into AnyRef, anonymous-class lambdas in "
            "older versions) can add overhead. Scala 3 and modern Scala libs lean on "
            "value classes and opaque types to recover performance."
        ),
        key_traits=[
            "Steady-state perf comparable to Java",
            "Heavier object churn unless code is allocation-aware",
            "Type-class encoding via implicits/given resolution",
        ],
        so_used_pct_2024=2.6, so_admired_pct_2024=50.0, tiobe_rank_2025=30,
        cold_start="Same JVM warmup as Java",
        peak_speed="Comparable to Java",
        memory_baseline="JVM + extra allocation in functional patterns",
    ),
    "ruby": LanguageInfo(
        name="ruby", display_name="Ruby",
        category="Interpreted — MRI bytecode VM; optional YJIT/MJIT",
        paradigms=["Object-oriented", "Multi-paradigm"],
        typing="Dynamically and strongly typed",
        gc="Yes — generational mark-and-sweep",
        runtime="MRI/YARV (default); TruffleRuby, JRuby",
        design_notes=(
            "Ruby's expressiveness and Smalltalk-like object model come with "
            "per-method-call overhead. Recent YJIT (Ruby 3.2+) speeds up hot paths "
            "considerably but the language still trails statically-typed peers on raw "
            "arithmetic. The ecosystem is biased toward web and scripting."
        ),
        key_traits=[
            "Late binding everywhere; method calls go through dynamic dispatch",
            "YJIT closes some of the gap on hot loops",
            "Object allocation is heavy",
        ],
        so_used_pct_2024=5.2, so_admired_pct_2024=50.0, tiobe_rank_2025=20,
        cold_start="Fast",
        peak_speed="~30–100× slower than C++ without JIT; YJIT improves hot paths 1.5–3×",
        memory_baseline="High — like Python, every value is a heap object",
    ),
    "php": LanguageInfo(
        name="php", display_name="PHP",
        category="Interpreted — Zend Engine; optional JIT (PHP 8+)",
        paradigms=["Multi-paradigm", "Object-oriented", "Procedural"],
        typing="Dynamically typed with optional declarations (PHP 7+)",
        gc="Reference counting + cyclic collector",
        runtime="Zend Engine (default); HHVM (formerly)",
        design_notes=(
            "PHP is purpose-built for the request/response model — each request gets a "
            "fresh state. The Zend Engine compiles to opcodes; PHP 8 added a JIT that "
            "speeds up CPU-heavy workloads. Outside of web request handling the "
            "language's tooling and performance profile are less polished."
        ),
        key_traits=[
            "Fast request lifecycle thanks to per-request state",
            "JIT closes the gap on CPU work but is rarely the bottleneck",
            "Function call overhead is non-trivial without JIT",
        ],
        so_used_pct_2024=18.2, so_admired_pct_2024=35.0, tiobe_rank_2025=17,
        cold_start="Fast",
        peak_speed="With JIT: within ~5–10× of C++ on tight loops",
        memory_baseline="Moderate — Zend Engine per-request overhead",
    ),
    "elixir": LanguageInfo(
        name="elixir", display_name="Elixir",
        category="Managed — compiled to BEAM bytecode (Erlang VM; BeamAsm JIT)",
        paradigms=["Functional", "Concurrent", "Immutable"],
        typing="Dynamically and strongly typed; optional typespecs (@spec)",
        gc="Per-process generational collector — no global stop-the-world pauses",
        runtime="BEAM (the Erlang VM); shares bytecode with Erlang",
        design_notes=(
            "Elixir compiles to the same BEAM bytecode as Erlang and inherits its "
            "lightweight processes and per-process garbage collector, so collection "
            "never stops the whole system. BeamAsm compiles hot bytecode to native "
            "code. Data is immutable, so updates allocate new terms; raw arithmetic "
            "trails the native and JVM languages, but concurrency, fault isolation, "
            "and tail-latency are the language's strengths."
        ),
        key_traits=[
            "Immutable data — frequent small allocations on the per-process heap",
            "Per-process GC: low, predictable pause times",
            "BeamAsm JIT narrows the gap on hot bytecode",
            "Built for concurrency and I/O, not tight numeric loops",
        ],
        so_used_pct_2024=2.6, so_admired_pct_2024=72.0, tiobe_rank_2025=40,
        cold_start="Fast — BEAM boots quickly",
        peak_speed="Several× to ~10× slower than C++ on arithmetic",
        memory_baseline="Moderate — per-process heaps + BEAM runtime",
    ),
    "erlang": LanguageInfo(
        name="erlang", display_name="Erlang",
        category="Managed — BEAM bytecode (Erlang VM; BeamAsm JIT)",
        paradigms=["Functional", "Concurrent"],
        typing="Dynamically and strongly typed; Dialyzer specs are optional",
        gc="Per-process generational collector — no global stop-the-world pauses",
        runtime="BEAM (the Erlang VM)",
        design_notes=(
            "Erlang was built for massively concurrent, fault-tolerant, soft-real-"
            "time systems. It runs on the BEAM with the same lightweight processes, "
            "per-process GC, and BeamAsm JIT as Elixir. Numeric throughput is not its "
            "goal; it wins on concurrency, distribution, and predictable latency."
        ),
        key_traits=[
            "Lightweight processes with isolated per-process heaps",
            "Per-process GC: no global pause, low latency",
            "BeamAsm JIT compiles hot bytecode to native",
            "Pattern matching and recursion over loops; immutable data",
        ],
        so_used_pct_2024=1.0, so_admired_pct_2024=57.0, tiobe_rank_2025=50,
        cold_start="Fast — BEAM boots quickly",
        peak_speed="Several× to ~10× slower than C++ on arithmetic",
        memory_baseline="Moderate — per-process heaps + BEAM runtime",
    ),
    "racket": LanguageInfo(
        name="racket", display_name="Racket",
        category="Compiled to bytecode on the Chez Scheme VM (Racket CS)",
        paradigms=["Functional", "Multi-paradigm", "Language-oriented"],
        typing="Dynamically typed; optional contracts (define/contract) and Typed Racket",
        gc="Yes — generational (inherited from the Chez Scheme runtime)",
        runtime="Racket CS — the Chez Scheme VM (since Racket 8)",
        design_notes=(
            "Racket is a Scheme descendant built for language-oriented programming. "
            "Since version 8 it compiles to Chez Scheme bytecode (Racket CS) with a "
            "generational collector. Its `define/contract` forms add runtime "
            "boundary checks (kept here, as that is the LeetCode Racket shape), and "
            "immutable pairs plus dynamic dispatch mean arithmetic-heavy code trails "
            "the native and JVM languages; its strength is expressiveness and macros."
        ),
        key_traits=[
            "S-expression homoiconicity and a powerful macro system",
            "Contracts add runtime checks at function boundaries",
            "Generational GC; singly-linked lists favor cons/recur over indexing",
            "Chez-backed JIT-style compilation; startup boots the VM + modules",
        ],
        so_used_pct_2024=1.1, so_admired_pct_2024=52.0, tiobe_rank_2025=50,
        cold_start="Moderate — VM boot + module load",
        peak_speed="Several× to ~15× slower than C++ on arithmetic",
        memory_baseline="Moderate — Racket CS runtime + heap",
    ),
}


def language_info_dict(info: LanguageInfo) -> dict:
    return asdict(info)
