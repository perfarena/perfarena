"""Populate the DB with fake competitors and judged submissions.

Use to bring the UI to life without running real LeetCode submissions.
Idempotent-ish: re-running adds another round of submissions for each model.

Run as: python -m app.populate
"""
from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta

from sqlmodel import Session, select

from .auth import generate_api_key
from .baselines import seed_baselines, LANG_RUNTIME_MULT
from .dispatch import REPEATS, _aggregate_attempt, _mean
from .db import engine, init_db
from .models import (
    Attempt, Baseline, Problem, Run, Submission, SubmissionStatus,
    User, UserRole, UserStatus,
)


# (display_name, org, email, models, languages, base_skill, perf_bias)
#   base_skill: 0..1 — probability the model solves an Easy problem
#   perf_bias:  bumps runtime/memory percentile means
SEEDS = [
    ("Acme Labs",      "Acme Labs",        "acme@labs.dev",
        [("acme-coder", "v3.2")],
        ["python3", "cpp"], 0.86, 70),
    ("Northwind AI",   "Northwind AI",     "north@labs.dev",
        [("north-l", "1.5"), ("north-xl", "1.5")],
        ["python3", "java"], 0.78, 55),
    ("Polaris Group",  "Polaris Group",    "polaris@labs.dev",
        [("polaris-pro", "0.9")],
        ["python3", "javascript"], 0.66, 45),
    ("Vela Research",  "Vela Research",    "vela@labs.dev",
        [("vela", "alpha"), ("vela-coder", "beta")],
        ["python3", "rust"], 0.92, 80),
    ("Open Spruce",    "Open Spruce",      "spruce@labs.dev",
        [("spruce-7b", "2026-04")],
        ["python3"], 0.52, 35),
    ("Halcyon",        "Halcyon",          "halc@labs.dev",
        [("halcyon-x", "rc1")],
        ["python3", "cpp", "java"], 0.88, 72),
]


SOLVE_PROB_BY_LEVEL = {"Easy": 1.0, "Medium": 0.7, "Hard": 0.45}


def _ensure_user(session: Session, display_name: str, org: str, email: str) -> User:
    user = session.exec(select(User).where(User.email == email)).first()
    if user:
        if user.status != UserStatus.approved:
            user.status = UserStatus.approved
            user.api_key = user.api_key or generate_api_key()
            user.approved_at = datetime.utcnow()
            session.add(user); session.commit(); session.refresh(user)
        return user
    user = User(
        email=email, display_name=display_name, org=org,
        role=UserRole.user, status=UserStatus.approved,
        api_key=generate_api_key(),
        approved_at=datetime.utcnow(),
    )
    session.add(user); session.commit(); session.refresh(user)
    return user


def _fake_attempt_code(slug: str, lang: str) -> str:
    return f"# generated solution for {slug} in {lang}\n# (placeholder)\n"


def _fake_trace(slug: str) -> str:
    return (
        f"Considered approaches for {slug}: brute force vs. optimized. "
        "Chose the optimized path: build helper structure, iterate once, "
        "track best candidate. Edge cases: empty input, single element, duplicates."
    )


def _judge_runs(level: str, skill: float, perf_bias: float) -> list[dict]:
    """Produce REPEATS run dicts for one problem: correctness decided once,
    timing/memory wobble per run like the real judge."""
    accept_p = skill * SOLVE_PROB_BY_LEVEL[level]
    if random.random() >= accept_p:
        status = random.choice(["Wrong Answer", "Time Limit Exceeded", "Runtime Error"])
        return [{
            "status_msg": status, "runtime_ms": None, "memory_mb": None,
            "runtime_percentile": None, "memory_percentile": None,
            "total_correct": random.randint(0, 28), "total_testcases": 30,
        } for _ in range(REPEATS)]
    base_rt = max(1.0, random.gauss(60, 30))
    base_mem = random.uniform(13.5, 22.0)
    rt_p = max(0.0, min(100.0, random.gauss(perf_bias, 12)))
    mem_p = max(0.0, min(100.0, random.gauss(perf_bias - 5, 14)))
    out = []
    for _ in range(REPEATS):
        out.append({
            "submission_id": str(random.randint(10**9, 10**10)),
            "status_msg": "Accepted",
            "runtime_ms": round(max(0.0, base_rt * random.uniform(0.85, 1.2)), 2),
            "memory_mb": round(max(13.0, base_mem * random.uniform(0.97, 1.04)), 3),
            "runtime_percentile": round(min(100.0, max(0.0, rt_p * random.uniform(0.95, 1.05))), 2),
            "memory_percentile": round(min(100.0, max(0.0, mem_p * random.uniform(0.95, 1.05))), 2),
            "total_correct": 30, "total_testcases": 30,
        })
    return out


def _make_batch(
    session: Session, user: User, model_name: str, model_version: str,
    language: str, problems: list[Problem], skill: float, perf_bias: float,
    age_days: int,
) -> Submission:
    created = datetime.utcnow() - timedelta(days=age_days, hours=random.randint(0, 23))
    sub = Submission(
        user_id=user.id, model_name=model_name, model_version=model_version,
        language=language,
        prompt_template=(
            "You are an expert competitive programmer. Solve the following LeetCode "
            "problem in {lang}. Return only the code; do not include test harnesses."
        ),
        model_params={
            "temperature": round(random.uniform(0.0, 0.4), 2),
            "top_p": round(random.uniform(0.8, 1.0), 2),
            "max_tokens": random.choice([2048, 4096, 8192]),
            "system_prompt_hash": "sha256:" + "".join(random.choices("0123456789abcdef", k=12)),
        },
        status=SubmissionStatus.judged,
        created_at=created,
    )
    session.add(sub); session.commit(); session.refresh(sub)

    solved = 0
    avg_rt = []; best_rt = []; avg_mem = []; best_mem = []
    avg_rtp = []; avg_memp = []
    t_h_avg = []; t_h_exp = []; m_h_avg = []; m_h_exp = []
    cost_sum = 0.0; gen_ms_sum = 0      # roll up cost + generation wall-clock
    baselines = {
        b.problem_id: b for b in session.exec(
            select(Baseline).where(
                Baseline.language == language,
                Baseline.problem_id.in_([p.id for p in problems]),
            )
        ).all()
    }
    for p in problems:
        gen_ms = random.randint(2_000, 30_000)
        cost = round(random.uniform(0.001, 0.05), 4)
        gen_ms_sum += gen_ms; cost_sum += cost
        attempt = Attempt(
            submission_id=sub.id, problem_id=p.id,
            code=_fake_attempt_code(p.title_slug, language),
            inference_trace=_fake_trace(p.title_slug),
            wall_clock_ms=gen_ms,
            cost_usd=cost,
        )
        session.add(attempt); session.commit(); session.refresh(attempt)

        runs = _judge_runs(p.level, skill, perf_bias)
        for i, r in enumerate(runs, start=1):
            session.add(Run(
                attempt_id=attempt.id, run_index=i,
                submission_id_external=r.get("submission_id"),
                status_msg=r["status_msg"],
                runtime_ms=r["runtime_ms"], memory_mb=r["memory_mb"],
                runtime_percentile=r["runtime_percentile"],
                memory_percentile=r["memory_percentile"],
                total_correct=r["total_correct"], total_testcases=r["total_testcases"],
                raw_response=r, created_at=created,
            ))
        _aggregate_attempt(attempt, runs)
        session.add(attempt); session.commit()

        if attempt.final_status == "Accepted":
            solved += 1
            if attempt.avg_runtime_ms is not None: avg_rt.append(attempt.avg_runtime_ms)
            if attempt.best_runtime_ms is not None: best_rt.append(attempt.best_runtime_ms)
            if attempt.avg_memory_mb is not None: avg_mem.append(attempt.avg_memory_mb)
            if attempt.best_memory_mb is not None: best_mem.append(attempt.best_memory_mb)
            if attempt.avg_runtime_percentile is not None: avg_rtp.append(attempt.avg_runtime_percentile)
            if attempt.avg_memory_percentile is not None: avg_memp.append(attempt.avg_memory_percentile)
            b = baselines.get(p.id)
            if b and attempt.avg_runtime_ms and attempt.avg_runtime_ms > 0:
                t_h_avg.append(b.runtime_ms_avg / attempt.avg_runtime_ms)
                t_h_exp.append(b.runtime_ms_expert / attempt.avg_runtime_ms)
            if b and attempt.avg_memory_mb and attempt.avg_memory_mb > 0:
                m_h_avg.append(b.memory_mb_avg / attempt.avg_memory_mb)
                m_h_exp.append(b.memory_mb_expert / attempt.avg_memory_mb)

    sub.total_problems = len(problems)
    sub.total_solved = solved
    sub.pass_at_1 = solved / len(problems) if problems else 0.0
    sub.avg_runtime_ms = _mean(avg_rt)
    sub.best_runtime_ms = _mean(best_rt)
    sub.avg_memory_mb = _mean(avg_mem)
    sub.best_memory_mb = _mean(best_mem)
    sub.avg_runtime_percentile = _mean(avg_rtp)
    sub.avg_memory_percentile = _mean(avg_memp)
    sub.time_human_avg = _mean(t_h_avg)
    sub.time_human_expert = _mean(t_h_exp)
    sub.mem_human_avg = _mean(m_h_avg)
    sub.mem_human_expert = _mean(m_h_exp)
    sub.total_cost_usd = round(cost_sum, 6) if problems else None
    sub.total_generation_ms = gen_ms_sum if problems else None
    session.add(sub); session.commit()
    return sub


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--problems", type=int, default=60,
                    help="how many problems each batch covers (sampled from DB)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    random.seed(args.seed)
    init_db()

    with Session(engine) as session:
        all_problems = session.exec(select(Problem)).all()
        if not all_problems:
            print("no problems in DB — run `python -m app.seed` first")
            return

        n_baselines = session.exec(select(Baseline)).first()
        if n_baselines is None:
            print("seeding LeetCode-population baselines …")
            inserted = seed_baselines(session, list(LANG_RUNTIME_MULT.keys()))
            print(f"  inserted {inserted} baseline rows")

        easy = [p for p in all_problems if p.level == "Easy"]
        med = [p for p in all_problems if p.level == "Medium"]
        hard = [p for p in all_problems if p.level == "Hard"]
        per_level = max(1, args.problems // 3)
        random.shuffle(easy); random.shuffle(med); random.shuffle(hard)
        sample = easy[:per_level] + med[:per_level] + hard[:per_level]
        print(f"using {len(sample)} problems per batch "
              f"({sum(p.level == 'Easy' for p in sample)} Easy / "
              f"{sum(p.level == 'Medium' for p in sample)} Medium / "
              f"{sum(p.level == 'Hard' for p in sample)} Hard)")

        n_subs = 0
        for display, org, email, models, langs, skill, perf in SEEDS:
            user = _ensure_user(session, display, org, email)
            for model_name, version in models:
                for lang in langs:
                    age = random.randint(0, 21)
                    _make_batch(session, user, model_name, version, lang,
                                sample, skill, perf, age_days=age)
                    n_subs += 1

        print(f"created {n_subs} judged submissions across {len(SEEDS)} competitors")


if __name__ == "__main__":
    main()
