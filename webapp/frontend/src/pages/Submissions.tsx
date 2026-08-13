import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, getApiKey, SubmissionPublic } from "../lib/api";
import SubmissionsTable from "../components/SubmissionsTable";
import { SubmitPanel } from "./Submit";

const PAGE_SIZE = 20;

/* Merged Submissions + Submit page.
 *
 * Top half (public): the global submissions table, paginated 20 rows per
 * page. Bottom half (gated): the submit flow — only rendered for logged-in
 * users; anonymous visitors get a call-to-action to register/log in. */
export default function Submissions() {
  const [rows, setRows] = useState<SubmissionPublic[]>([]);
  const [err, setErr] = useState("");
  const [page, setPage] = useState(0);
  // Client-side filters. The list endpoint returns every submission with
  // its harness / competition already stamped, so filtering is a cheap
  // in-memory partition rather than a re-fetch. Hydrated from ?harness=…
  // / ?competition=… so a deep link from HarnessDetail / CompetitionDetail
  // lands scoped correctly.
  const initial = () => {
    if (typeof window === "undefined") return { h: "", c: "", co: "" };
    const p = new URLSearchParams(window.location.search);
    return {
      h: p.get("harness") ?? "",
      c: p.get("competition") ?? "",
      co: p.get("correctness_oracle") ?? "",
    };
  };
  const [harnessSlug, setHarnessSlug] = useState<string>(initial().h);
  const [competitionSlug, setCompetitionSlug] = useState<string>(initial().c);
  const [correctnessOracleSlug, setCorrectnessOracleSlug] = useState<string>(initial().co);

  useEffect(() => {
    api.submissions().then(setRows).catch((e: unknown) => setErr(String(e)));
  }, []);

  // Universes for the dropdowns are derived from the returned rows so we
  // never advertise a slug the current dataset doesn't contain.
  const { harnesses, competitions, correctnessOracles } = useMemo(() => {
    const h = new Map<string, string>();
    const c = new Map<string, string>();
    const co = new Map<string, string>();
    for (const r of rows) {
      if (r.harness_slug) h.set(r.harness_slug, r.harness_name ?? r.harness_slug);
      if (r.competition_slug) c.set(r.competition_slug, r.competition_name ?? r.competition_slug);
      if (r.correctness_oracle_slug) {
        co.set(r.correctness_oracle_slug,
              r.correctness_oracle_name ?? r.correctness_oracle_slug);
      }
    }
    const byName = (a: [string, string], b: [string, string]) => a[1].localeCompare(b[1]);
    return {
      harnesses: [...h.entries()].sort(byName),
      competitions: [...c.entries()].sort(byName),
      correctnessOracles: [...co.entries()].sort(byName),
    };
  }, [rows]);

  const filtered = useMemo(() => rows.filter(r => {
    if (harnessSlug && r.harness_slug !== harnessSlug) return false;
    if (competitionSlug && r.competition_slug !== competitionSlug) return false;
    if (correctnessOracleSlug && r.correctness_oracle_slug !== correctnessOracleSlug) return false;
    return true;
  }), [rows, harnessSlug, competitionSlug, correctnessOracleSlug]);

  useEffect(() => { setPage(0); }, [harnessSlug, competitionSlug, correctnessOracleSlug]);

  const loggedIn = !!getApiKey();
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const start = safePage * PAGE_SIZE;
  const paged = filtered.slice(start, start + PAGE_SIZE);

  return (
    <div className="col" style={{ gap: 24 }}>
      <div className="hero">
        <h1>Submissions</h1>
        <p>
          This page lists every batch that approved competitors submitted.
          {loggedIn ? " Start or continue your own batch below." : " Log in to submit your own batch."}
        </p>
      </div>

      {err && <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>}

      <section className="col" style={{ gap: 10 }}>
        <div className="filter-bar">
          <select value={harnessSlug} onChange={e => setHarnessSlug(e.target.value)}
                  title="Show only the submissions that one performance oracle (harness) scored. Use the deep link ?harness=slug.">
            <option value="">All performance oracles</option>
            {harnesses.map(([slug, name]) => (
              <option key={slug} value={slug}>{name}</option>
            ))}
          </select>
          <select value={correctnessOracleSlug}
                  onChange={e => setCorrectnessOracleSlug(e.target.value)}
                  title="Show only the submissions that one correctness oracle (the verdict source) checked. Use the deep link ?correctness_oracle=slug.">
            <option value="">All correctness oracles</option>
            {correctnessOracles.map(([slug, name]) => (
              <option key={slug} value={slug}>{name}</option>
            ))}
          </select>
          <select value={competitionSlug} onChange={e => setCompetitionSlug(e.target.value)}
                  title="Show only the submissions for one competition. Use the deep link ?competition=slug.">
            <option value="">All competitions</option>
            {competitions.map(([slug, name]) => (
              <option key={slug} value={slug}>{name}</option>
            ))}
          </select>
          {(harnessSlug || competitionSlug || correctnessOracleSlug) && (
            <button className="secondary"
                    onClick={() => {
                      setHarnessSlug("");
                      setCompetitionSlug("");
                      setCorrectnessOracleSlug("");
                    }}>
              Clear filters
            </button>
          )}
        </div>
        <SubmissionsTable rows={paged} />
        {filtered.length > 0 && (
          <Pagination
            page={safePage}
            totalPages={totalPages}
            total={filtered.length}
            start={start}
            shown={paged.length}
            onPage={setPage}
          />
        )}
      </section>

      {loggedIn ? (
        <SubmitPanel />
      ) : (
        <div className="card col" style={{ gap: 8 }}>
          <h2 style={{ margin: 0 }}>Do you want to submit your own model?</h2>
          <p className="muted" style={{ margin: 0 }}>
            Submissions are open to approved competitors.{" "}
            <Link to="/register">Request access</Link> or{" "}
            <Link to="/login">log in</Link> to start a batch. The submit form
            and the API documentation appear here after you sign in.
          </p>
        </div>
      )}
    </div>
  );
}

function Pagination({
  page, totalPages, total, start, shown, onPage,
}: {
  page: number; totalPages: number; total: number;
  start: number; shown: number; onPage: (p: number) => void;
}) {
  return (
    <div className="row" style={{ justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
      <span className="muted" style={{ fontSize: "0.85rem" }}>
        Rows {total === 0 ? 0 : start + 1} to {start + shown} of {total}
      </span>
      <div className="row" style={{ gap: 6 }}>
        <button className="secondary" disabled={page <= 0} onClick={() => onPage(0)}>« First</button>
        <button className="secondary" disabled={page <= 0} onClick={() => onPage(page - 1)}>‹ Previous</button>
        <span className="muted" style={{ padding: "0 8px", alignSelf: "center" }}>
          Page {page + 1} / {totalPages}
        </span>
        <button className="secondary" disabled={page >= totalPages - 1} onClick={() => onPage(page + 1)}>Next ›</button>
        <button className="secondary" disabled={page >= totalPages - 1} onClick={() => onPage(totalPages - 1)}>Last »</button>
      </div>
    </div>
  );
}
