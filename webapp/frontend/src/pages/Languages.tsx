import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, LanguageListItem } from "../lib/api";
import { formatPassAt1, formatRuntime } from "../lib/format";

/* /languages — overview of every language the platform tracks, one card
 * each. Designed to be the "where do I want to look" page: paradigm + TIOBE
 * rank for at-a-glance positioning, activity counts (submissions, distinct
 * models) so the operator knows whether there's data worth drilling into,
 * and the top-3 submissions by pass@k so a quick "who's winning here" read
 * is one glance away. Clicking the card body navigates to /languages/:name
 * for the full leaderboard + design notes.
 *
 * Layout is grid-of-cards rather than a table: paradigm is a list, top-3
 * is a sub-table, and TIOBE is a single rank — none of those tabulate
 * cleanly. Cards keep each language's section visually self-contained. */
export default function Languages() {
  const [rows, setRows] = useState<LanguageListItem[]>([]);
  const [err, setErr] = useState("");
  const [hideEmpty, setHideEmpty] = useState(true);

  useEffect(() => {
    api.languages()
      .then(setRows)
      .catch((e: unknown) => setErr(String(e)));
  }, []);

  const visible = hideEmpty ? rows.filter(r => r.n_submissions > 0) : rows;

  if (err) {
    return <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>;
  }

  return (
    <div className="col" style={{ gap: 18 }}>
      <div className="hero" style={{ paddingBottom: 0 }}>
        <h1>Languages</h1>
        <p>
          This page lists every language that the platform tracks. Each card
          shows the paradigm, the TIOBE rank, and the current top submissions.
          Click a card to open the full leaderboard for the language. The
          leaderboard has sortable metric columns.
        </p>
      </div>

      <div className="row" style={{ justifyContent: "flex-end", gap: 8 }}>
        <label className="row" style={{ gap: 6, fontSize: "0.9rem" }}>
          <input type="checkbox" checked={hideEmpty}
                 onChange={e => setHideEmpty(e.target.checked)} />
          hide languages that have no submissions
        </label>
      </div>

      {rows.length === 0 ? (
        <div className="empty">Please wait.</div>
      ) : visible.length === 0 ? (
        <div className="empty">
          No language has a submission yet. Clear the filter to see all
          languages.
        </div>
      ) : (
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))",
          gap: 14,
        }}>
          {visible.map(l => <LanguageCard key={l.name} l={l} />)}
        </div>
      )}
    </div>
  );
}


function LanguageCard({ l }: { l: LanguageListItem }) {
  const href = `/languages/${encodeURIComponent(l.name)}`;
  // The whole card is one big link; making the *card* clickable instead
  // of just the heading saves the operator a precise click. We still
  // wrap the top-3 row's "details →" cell in its own Link so a click
  // inside the table can jump straight to the submission.
  return (
    <Link to={href} className="card col" style={{
      gap: 10, textDecoration: "none",
      transition: "border-color 120ms",
    }}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 6 }}>
        <div className="col" style={{ gap: 2 }}>
          <h3 style={{ margin: 0 }}>{l.display_name}</h3>
          <div className="muted" style={{ fontSize: "0.78rem" }}>{l.category}</div>
        </div>
        {l.tiobe_rank_2025 != null && (
          <div className="col" style={{ alignItems: "flex-end", gap: 2 }}>
            <div className="muted" style={{ fontSize: "0.7rem", textTransform: "uppercase", letterSpacing: 0.5 }}>
              TIOBE 2025
            </div>
            <div style={{ fontWeight: 700 }}>#{l.tiobe_rank_2025}</div>
          </div>
        )}
      </div>

      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        {(l.paradigms ?? []).map(p => (
          <span key={p} className="badge"
                style={{
                  fontSize: "0.7rem", padding: "1px 8px",
                  background: "var(--panel-2)", border: "1px solid var(--border)",
                  borderRadius: 999,
                }}>
            {p}
          </span>
        ))}
        {(l.paradigms ?? []).length === 0 && (
          <span className="muted" style={{ fontSize: "0.8rem" }}>
            no paradigm data
          </span>
        )}
      </div>

      <div className="row" style={{ gap: 14, flexWrap: "wrap" }}>
        <Mini label="submissions"  value={String(l.n_submissions)} />
        <Mini label="fully accepted" value={String(l.n_fully_accepted)} />
        <Mini label="models"       value={String(l.n_models)} />
      </div>

      {(l.top_submissions ?? []).length === 0 ? (
        <div className="muted" style={{ fontSize: "0.85rem", fontStyle: "italic" }}>
          This language has no submissions with results yet.
        </div>
      ) : (
        <div className="col" style={{ gap: 4 }}>
          <div className="muted" style={{ fontSize: "0.7rem", textTransform: "uppercase", letterSpacing: 0.5 }}>
            Top by pass@k
          </div>
          <table className="tbl" style={{ fontSize: "0.85rem" }}>
            <thead>
              <tr>
                <th>Model</th>
                <th title="The mean acceptance across the submitted attempts.">pass@k</th>
                <th title="pass@1 is the acceptance on the first attempt.">p@1</th>
                <th title="The average runtime that LeetCode reports across the accepted attempts.">Runtime</th>
              </tr>
            </thead>
            <tbody>
              {(l.top_submissions ?? []).map(s => (
                <tr key={s.submission_id}>
                  <td>
                    <b>{s.model_name}</b>
                    <span className="muted" style={{ fontSize: "0.8em" }}> {s.model_version}</span>
                  </td>
                  <td className="metric">{formatPassAt1(s.pass_at_k)}</td>
                  <td className="metric">{formatPassAt1(s.pass_at_1)}</td>
                  <td className="metric">{formatRuntime(s.avg_runtime_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Link>
  );
}


function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="col" style={{ gap: 2 }}>
      <div className="muted" style={{ fontSize: "0.7rem", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontWeight: 600 }}>{value}</div>
    </div>
  );
}
