import { Link, useSearchParams } from "react-router-dom";
import Harnesses from "./Harnesses";
import CorrectnessOracles from "./CorrectnessOracles";

/* /harnesses — the two-oracle hub. Both sides of the model live here as
 * tabs so an operator can pick one page in the nav and manage everything
 * that produces verdicts + performance numbers from one place.
 *
 * Active tab persists in ?tab= so links are shareable; the /correctness-
 * oracles list route redirects onto ?tab=correctness. Detail routes
 * (/harnesses/:slug and /correctness-oracles/:slug) stay separate — a
 * deep link to one specific oracle should land on its own page, not the
 * hub. */

type Tab = "perf" | "correctness";

const VALID_TABS = new Set<Tab>(["perf", "correctness"]);

export default function HarnessesHub() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab") ?? "perf";
  const tab: Tab = VALID_TABS.has(raw as Tab) ? (raw as Tab) : "perf";

  function setTab(next: Tab) {
    // Preserve every other query param so a filter state living in the URL
    // (future-proofing) survives a tab flip. `perf` is the default so we
    // drop the key rather than serialize a redundant value.
    const p = new URLSearchParams(params);
    if (next === "perf") p.delete("tab");
    else p.set("tab", next);
    setParams(p, { replace: false });
  }

  return (
    <div className="col" style={{ gap: 16 }}>
      <div className="hero" style={{ paddingBottom: 8 }}>
        <h1 style={{ marginBottom: 4 }}>Harnesses</h1>
        <p style={{ margin: 0 }}>
          A competition scores each solution with two oracles. A{" "}
          <b>performance oracle</b> measures how a solution runs (runtime,
          memory, percentiles, energy). A <b>correctness oracle</b> decides
          whether it solved the problem at all
          (Accepted / Wrong Answer / Runtime Error / TLE / Compile Error).
          LeetCode's online judge does both today, so the seeded{" "}
          <code>leetcode</code> row on each tab covers every legacy
          submission. Add a new harness on the tab where it belongs (for
          example, an in-house RAPL-instrumented runner for performance or a
          HumanEval unit-test suite for correctness). Competitions and
          submissions bind the two kinds of oracle independently.
        </p>
      </div>

      <div className="row" style={{ gap: 4, borderBottom: "1px solid var(--border)" }}>
        <TabBtn active={tab === "perf"}
                onClick={() => setTab("perf")}>
          Performance oracles
        </TabBtn>
        <TabBtn active={tab === "correctness"}
                onClick={() => setTab("correctness")}>
          Correctness oracles
        </TabBtn>
        <span style={{ marginLeft: "auto", alignSelf: "center" }} className="muted">
          <Link to={tab === "perf" ? "/harnesses/leetcode" : "/correctness-oracles/leetcode"}
                style={{ fontSize: "0.85rem" }}>
            Open the seeded LeetCode oracle →
          </Link>
        </span>
      </div>

      {tab === "perf"        && <Harnesses />}
      {tab === "correctness" && <CorrectnessOracles />}
    </div>
  );
}

function TabBtn({
  active, onClick, children,
}: {
  active: boolean; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={active ? undefined : "secondary"}
      style={{
        borderRadius: "8px 8px 0 0",
        borderBottom: active ? "2px solid var(--accent)" : "2px solid transparent",
        background: active ? "var(--accent-soft)" : "transparent",
        color: active ? "var(--fg)" : "var(--fg-dim)",
        fontWeight: active ? 700 : 500,
        padding: "8px 16px",
        border: active ? undefined : "none",
      }}
    >
      {children}
    </button>
  );
}
