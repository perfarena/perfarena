import { AttemptProvenance, AttemptTrace, RunErrorLog } from "../lib/api";

/** Shared inline panels for per-row audit info on SubmissionDetail and
 *  AttemptResults — keeps the two pages visually consistent. */

export function hasErrorLog(log: RunErrorLog | null | undefined): boolean {
  return !!log && Object.values(log).some(v => v != null && v !== "");
}

export function sigBadge(s: string | null | undefined) {
  const map: Record<string, [string, string]> = {
    verified: ["badge-easy", "signature ✓ verified"],
    invalid: ["badge-hard", "signature ✗ invalid"],
    unsigned: ["badge-medium", "unsigned"],
    unsupported: ["badge-medium", "signature not checked"],
  };
  const [cls, label] = map[s ?? "unsigned"] ?? ["badge-medium", "unsigned"];
  return <span className={`badge ${cls}`}>{label}</span>;
}

export function ErrorPanel({ log }: { log: RunErrorLog }) {
  const fields: Array<[string, string | null | undefined]> = [
    ["Error log", log.error_log],
    ["Runtime error", log.full_runtime_error || log.runtime_error],
    ["Compile error", log.full_compile_error || log.compile_error],
    ["Failing test case (input)", log.last_testcase],
    ["Expected output", log.expected_output],
    ["Your output", log.code_output],
    ["Stdout", log.std_output],
    ["Dispatch note", log.note],
    ["leetbot stdout (tail)", log.raw_stdout],
    ["leetbot stderr (tail)", log.raw_stderr],
  ].filter(([, v]) => v) as Array<[string, string]>;
  if (fields.length === 0) return <div className="muted">No error details captured.</div>;
  return (
    <div className="col" style={{ gap: 10, padding: 10 }}>
      {fields.map(([label, value]) => (
        <div key={label} className="col" style={{ gap: 4 }}>
          <div className="muted" style={{ fontSize: "0.78rem", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
          <pre className="code" style={{ margin: 0, maxHeight: 280, overflow: "auto", whiteSpace: "pre-wrap" }}>{value}</pre>
        </div>
      ))}
    </div>
  );
}

export function ProvenancePanel({ t }: {
  t: {
    trace?: AttemptTrace["trace"] | null;
    inference_trace?: string | null;
    provenance?: AttemptProvenance | null;
  };
}) {
  const tr = t.trace;
  const pv = t.provenance;
  const fields: Array<[string, string | null | undefined]> = [
    ["Prompt", tr?.prompt],
    ["System prompt", tr?.system_prompt],
    ["Reasoning", tr?.reasoning],
    ["Raw response", tr?.raw_response],
    ["Inference trace (summary)", t.inference_trace],
    ["Provider metadata", tr?.provider_meta ? JSON.stringify(tr.provider_meta, null, 2) : null],
  ].filter(([, v]) => v != null && v !== "") as Array<[string, string]>;
  return (
    <div className="col" style={{ gap: 10, padding: 10 }}>
      {tr && (
        <div className="muted" style={{ fontSize: "0.78rem" }}>
          {tr.provider ?? "?"}/{tr.model ?? "?"}
          {tr.endpoint ? ` · ${tr.endpoint}` : ""}
          {tr.created_at ? ` · ${new Date(tr.created_at).toLocaleString()}` : ""}
          {pv && <> · {sigBadge(pv.sig_status)}</>}
        </div>
      )}
      {fields.map(([label, value]) => (
        <div key={label} className="col" style={{ gap: 4 }}>
          <div className="muted" style={{ fontSize: "0.78rem", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
          <pre className="code" style={{ margin: 0, maxHeight: 280, overflow: "auto", whiteSpace: "pre-wrap" }}>{value}</pre>
        </div>
      ))}
      {fields.length === 0 && <div className="muted">No structured trace recorded for this attempt.</div>}
    </div>
  );
}

export function LogEntryPanel({ pv }: { pv: AttemptProvenance }) {
  const rows: Array<[string, string]> = [
    ["Seq", pv.log_seq != null ? `#${pv.log_seq}` : "—"],
    ["Logged at", pv.logged_at ? new Date(pv.logged_at).toLocaleString() : "—"],
    ["Signature", pv.sig_status ?? "—"],
    ["Trace signer", pv.trace_signer ?? "—"],
    ["Model digest", pv.model_digest ?? "—"],
    ["Digest source", pv.model_digest_source ?? "—"],
    ["Leaf hash", pv.leaf_hash ?? pv.trace_hash ?? "—"],
    ["Prev hash", pv.prev_hash ?? "—"],
    ["Entry hash", pv.entry_hash ?? "—"],
  ];
  return (
    <div className="col" style={{ gap: 10, padding: 10 }}>
      <div className="muted" style={{ fontSize: "0.78rem" }}>
        Append-only transparency-log entry for this attempt. Tampering with
        any field downstream breaks every later <code>entry_hash</code>.
      </div>
      <table className="tbl" style={{ margin: 0 }}>
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k}>
              <td className="muted" style={{ width: 160 }}>{k}</td>
              <td><code style={{ wordBreak: "break-all" }}>{v}</code></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
