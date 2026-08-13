import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ProgressEntryPayload } from "../lib/api";

const NEW_SAMPLE = `{
  "model_name": "example-model",
  "model_version": "2026-05",
  "language": "python3",
  "prompt_template": "Solve this LeetCode problem in {{lang}}. Return code only.",
  "model_params": { "temperature": 0.2, "max_tokens": 4096, "top_p": 0.9 },
  "attempts": [
    {
      "title_slug": "two-sum",
      "code": "class Solution:\\n    def twoSum(self, nums, target):\\n        seen = {}\\n        for i, n in enumerate(nums):\\n            if target - n in seen: return [seen[target - n], i]\\n            seen[n] = i",
      "inference_trace": "Use a hash map to remember each number's index, then check the complement on each step."
    }
  ]
}`;

/* The submit flow — your in-progress submissions, the create-new form, and
 * the API docs. Rendered as the lower (gated) half of the merged Submissions
 * page; only shown to logged-in users. Carries no hero of its own — the host
 * page supplies the page heading. */
export function SubmitPanel() {
  const [progress, setProgress] = useState<ProgressEntryPayload[]>([]);
  const [raw, setRaw] = useState(NEW_SAMPLE);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  const [err, setErr] = useState("");

  async function refresh() {
    try {
      setProgress(await api.myProgress());
    } catch (e: unknown) {
      setErr(String(e));
    }
  }
  useEffect(() => { refresh(); }, []);

  async function startBatch() {
    setErr(""); setResult(""); setBusy(true);
    try {
      const body = JSON.parse(raw);
      const out = await api.submitBatch(body);
      setResult(`Submission #${out.submission_id} started. The platform dispatches ${out.attempts_received} attempts now.`);
      await refresh();
    } catch (e: unknown) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  const byLanguage = new Map<string, ProgressEntryPayload[]>();
  progress.forEach(p => {
    const arr = byLanguage.get(p.language) ?? [];
    arr.push(p);
    byLanguage.set(p.language, arr);
  });

  return (
    <div className="col" style={{ gap: 24 }}>
      <section className="col">
        <h2>Submit the solutions of your model</h2>
        <p className="muted" style={{ marginTop: 0 }}>
          A submission is <b>fully accepted</b> when every supported problem has a solution
          that LeetCode accepts in at least one language. Start with a partial batch. Add the
          other solutions later. This page shows your progress for each language below.
        </p>
      </section>

      <section className="col">
        <h2>Your submissions in progress</h2>
        {progress.length === 0 ? (
          <div className="empty">You have no submissions yet. Start one below.</div>
        ) : (
          <div className="col" style={{ gap: 14 }}>
            {Array.from(byLanguage.entries()).map(([lang, entries]) => (
              <LanguageGroup key={lang} language={lang} entries={entries} onRefresh={refresh} />
            ))}
          </div>
        )}
      </section>

      <section className="col">
        <h2>Start a new submission</h2>
        <div className="card">
          <p className="muted" style={{ marginTop: 0 }}>
            Send one JSON payload. It contains a model header and a first list of attempts.
            The list can be partial. Each attempt must contain <code>title_slug</code>, <code>code</code>, and <code>inference_trace</code> (20 characters or more).
            Add more attempts later from the submission page.
          </p>
        </div>
        <div className="card col">
          <textarea rows={16} value={raw} onChange={e => setRaw(e.target.value)} className="code" />
          <div className="row">
            <button onClick={startBatch} disabled={busy}>{busy ? "Please wait…" : "Create submission"}</button>
            <button className="secondary" onClick={() => setRaw(NEW_SAMPLE)} disabled={busy}>Reset to sample</button>
          </div>
          {result && <div className="muted" style={{ color: "var(--accent-2)" }}>{result}</div>}
          {err && <div className="muted" style={{ color: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}
        </div>
      </section>

      <ApiDocs />
    </div>
  );
}

function ApiDocs() {
  const [open, setOpen] = useState(false);
  const base = window.location.origin;
  return (
    <section className="col">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <h2 style={{ margin: 0 }}>Use the API directly</h2>
        <button className="secondary" onClick={() => setOpen(o => !o)}>
          {open ? "Hide" : "Show"} API docs
        </button>
      </div>
      {open && (
        <div className="card col" style={{ gap: 14 }}>
          <p className="muted" style={{ margin: 0 }}>
            The UI does each task with a plain HTTP call. Authenticate with{" "}
            <code>Authorization: Bearer &lt;your-api-key&gt;</code>. The file{" "}
            <code>webapp/examples/submit_with_local_model.py</code> gives a complete
            client that you can run. It drives a local model that is compatible with
            OpenAI, submits the solutions, then prints the results.
          </p>

          <Doc title="1 · List supported problems (public)">
{`curl ${base}/api/problems | jq '.[].title_slug'`}
          </Doc>

          <Doc title="2 · Fetch one problem statement (public)">
{`curl ${base}/api/problems/two-sum`}
          </Doc>

          <Doc title="3 · Create a submission (partial allowed)">
{`curl -X POST ${base}/api/submissions \\
  -H "Authorization: Bearer $ARENA_API_KEY" \\
  -H 'Content-Type: application/json' \\
  -d '{
    "model_name": "my-model",
    "model_version": "2026-05",
    "language": "python3",
    "prompt_template": "Solve in {{lang}}. Code only.",
    "model_params": {"temperature": 0.2},
    "attempts": [{
      "title_slug": "two-sum",
      "code": "class Solution:\\n    def twoSum(self, nums, target): ...",
      "inference_trace": "hash map of value->index; check complement on each step",
      "trace": {
        "prompt": "<exact prompt sent to the model>",
        "system_prompt": "<system prompt, if any>",
        "raw_response": "<full unedited model output>",
        "reasoning": "<thinking trace, if the model emits one>",
        "model": "qwen2.5-coder:7b",
        "provider": "ollama",
        "endpoint": "http://localhost:11434",
        "params": {"temperature": 0.2},
        "created_at": "2026-05-15T12:00:00Z"
      },
      "model_digest": "ollama:sha256-<...>"
    }]
  }'
# → {"submission_id": 42, "status": "received", "attempts_received": 1}`}
          </Doc>

          <Doc title="Provenance trace (auditable)">
{`Each attempt may carry a structured "trace" so reviewers can certify the
model produced the solution. It is stored, shown on the submission page,
and included in the dataset export. Recommended keys:

  prompt          exact user prompt sent to the model
  system_prompt   system prompt used
  raw_response    the model's full, unedited output
  reasoning       thinking/chain-of-thought, if available
  model provider endpoint params created_at provider_meta

See webapp/examples/submit_with_local_model.py for an Ollama client
that captures all of this automatically.`}
          </Doc>

          <Doc title="Provenance integrity (hash chain + digest + signature)">
{`Per attempt you may also send:

  model_digest    explicit model identity — Ollama "ollama:<sha256>",
                  an HF revision SHA, an OpenAI system_fingerprint, …
                  Omit it and the platform derives "derived:<sha256>"
                  from model name/version/params/provider metadata.
  trace_sig       base64 Ed25519 signature over the LEAF HASH (below)
  trace_signer    base64 Ed25519 public key matching trace_sig

LEAF HASH = sha256(canonical_json({code, inference_trace, trace})) where
canonical_json = JSON with sorted keys, separators (",",":") , UTF-8.

On ingest the platform recomputes the leaf hash, verifies the signature,
and appends a server-timestamped entry to a public APPEND-ONLY hash chain.
Tampering, reordering, or back-dating any entry breaks every later hash —
so a trace cannot be rewritten after results are known. The server echoes
provenance back per attempt (trace_hash, sig_status, model_digest,
model_digest_source, log_seq, logged_at).`}
          </Doc>

          <Doc title="Audit the transparency log (public)">
{`curl ${base}/api/provenance/log?limit=20
# → { count, head, genesis, verified,
#     entries:[{seq, leaf_hash, prev_hash, entry_hash, logged_at,
#               model_digest, sig_status, ...}] }
# entry_hash = sha256(canonical_json(
#   {seq, prev_hash, leaf_hash, attempt_id, logged_at}))
# prev_hash of seq N must equal entry_hash of seq N-1 (genesis = 64 zeros).`}
          </Doc>

          <Doc title="4 · Append more attempts (incremental)">
{`curl -X POST ${base}/api/submissions/42/attempts \\
  -H "Authorization: Bearer $ARENA_API_KEY" \\
  -H 'Content-Type: application/json' \\
  -d '{"attempts": [
    {"title_slug": "add-digits", "code": "...", "inference_trace": "digital root mod 9 ..."}
  ]}'`}
          </Doc>

          <Doc title="5 · Check progress (what's missing per submission)">
{`curl ${base}/api/submissions/42/progress
# → {"accepted": N, "queued": N, "missing": [...slugs],
#    "rejected": [...], "fully_accepted": false}

# all your submissions at once:
curl -H "Authorization: Bearer $ARENA_API_KEY" ${base}/api/me/progress`}
          </Doc>

          <Doc title="6 · Read results">
{`curl ${base}/api/submissions/42
# avg_/best_ runtime & memory + per-problem runs.
# Each attempt is dispatched to LeetCode 5×; avg = mean of runs,
# best = each problem's fastest/leanest run.`}
          </Doc>

          <Doc title="7 · Download the solutions dataset (gated)">
{`curl -H "Authorization: Bearer $ARENA_API_KEY" \\
  "${base}/api/datasets/solutions?language=python3&only_accepted=true" \\
  -o arena_solutions.jsonl`}
          </Doc>

          <p className="muted" style={{ margin: 0 }}>
            A submission is <b>fully accepted</b> when every supported
            problem has an accepted solution in at least one language. Loop over{" "}
            <code>/api/problems</code> and append attempts. Continue until{" "}
            <code>/api/submissions/&#123;id&#125;/progress</code> reports{" "}
            <code>fully_accepted: true</code>.
          </p>
        </div>
      )}
    </section>
  );
}

function Doc({ title, children }: { title: string; children: string }) {
  return (
    <div className="col" style={{ gap: 6 }}>
      <div className="muted" style={{ fontSize: "0.8rem", textTransform: "uppercase", letterSpacing: 0.5 }}>{title}</div>
      <pre className="code" style={{ margin: 0 }}>{children}</pre>
    </div>
  );
}

function LanguageGroup({
  language, entries, onRefresh,
}: {
  language: string; entries: ProgressEntryPayload[]; onRefresh: () => void;
}) {
  const completed = entries.find(e => e.fully_accepted);
  return (
    <div className="card col" style={{ gap: 8 }}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <h3 style={{ margin: 0 }}>
          {language}
          {completed && (
            <span className="badge badge-easy" style={{ marginLeft: 10 }}>fully accepted</span>
          )}
        </h3>
        <span className="muted">{entries.length} submission{entries.length === 1 ? "" : "s"}</span>
      </div>
      <div className="col" style={{ gap: 10 }}>
        {entries.map(e => <ProgressRow key={e.submission_id} entry={e} onRefresh={onRefresh} />)}
      </div>
    </div>
  );
}

function ProgressRow({ entry, onRefresh }: { entry: ProgressEntryPayload; onRefresh: () => void }) {
  const pct = entry.total_supported > 0 ? (entry.accepted / entry.total_supported) * 100 : 0;
  return (
    <div className="col" style={{ gap: 6 }}>
      <div className="row" style={{ justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
        <div>
          <Link to={`/submit/${entry.submission_id}`}>
            <b>{entry.model_name}</b> <span className="muted">{entry.model_version}</span>
          </Link>
          <span className="muted"> · #{entry.submission_id}</span>
        </div>
        <div className="muted metric">
          {entry.accepted} accepted · {entry.queued} queued · {entry.missing_count} missing · {entry.total_supported} total
        </div>
      </div>
      <div className="progress-bar" title={`${pct.toFixed(1)}% accepted`}>
        <div className="progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="row" style={{ gap: 16 }}>
        <Link to={`/submit/${entry.submission_id}`}>Continue the submission →</Link>
        <Link to={`/submit/${entry.submission_id}/attempts`}>Attempt results →</Link>
      </div>
    </div>
  );
}
