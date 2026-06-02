import { useEffect, useState } from "react";
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

  useEffect(() => {
    api.submissions().then(setRows).catch((e: unknown) => setErr(String(e)));
  }, []);

  const loggedIn = !!getApiKey();
  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const start = safePage * PAGE_SIZE;
  const paged = rows.slice(start, start + PAGE_SIZE);

  return (
    <div className="col" style={{ gap: 24 }}>
      <div className="hero">
        <h1>Submissions</h1>
        <p>
          Every batch submitted by approved competitors.
          {loggedIn ? " Start or continue your own below." : " Log in to submit your own."}
        </p>
      </div>

      {err && <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>}

      <section className="col" style={{ gap: 10 }}>
        <SubmissionsTable rows={paged} />
        {rows.length > 0 && (
          <Pagination
            page={safePage}
            totalPages={totalPages}
            total={rows.length}
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
          <h2 style={{ margin: 0 }}>Want to submit your own model?</h2>
          <p className="muted" style={{ margin: 0 }}>
            Submissions are open to approved competitors.{" "}
            <Link to="/register">Request access</Link> or{" "}
            <Link to="/login">log in</Link> to start a batch — the submit form
            and API docs appear here once you're signed in.
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
        Showing {total === 0 ? 0 : start + 1}–{start + shown} of {total}
      </span>
      <div className="row" style={{ gap: 6 }}>
        <button className="secondary" disabled={page <= 0} onClick={() => onPage(0)}>« First</button>
        <button className="secondary" disabled={page <= 0} onClick={() => onPage(page - 1)}>‹ Prev</button>
        <span className="muted" style={{ padding: "0 8px", alignSelf: "center" }}>
          Page {page + 1} / {totalPages}
        </span>
        <button className="secondary" disabled={page >= totalPages - 1} onClick={() => onPage(page + 1)}>Next ›</button>
        <button className="secondary" disabled={page >= totalPages - 1} onClick={() => onPage(totalPages - 1)}>Last »</button>
      </div>
    </div>
  );
}
