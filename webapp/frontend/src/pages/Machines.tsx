import { useEffect, useState } from "react";
import { api, MachinePublic } from "../lib/api";

/* /machines — the physical/virtual boxes submissions were measured on. Energy
 * (and runtime) are only comparable within ONE machine + one energy tool, so
 * the machine is part of every measurement's provenance. Read-only: rows are
 * created automatically when a runner reports its descriptor during dispatch. */
export default function Machines() {
  const [rows, setRows] = useState<MachinePublic[] | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.machines().then(setRows).catch((e: unknown) => setErr(String(e)));
  }, []);

  return (
    <div className="col">
      <h1 style={{ marginBottom: 4 }}>Machines</h1>
      <div className="muted" style={{ fontSize: "0.9rem", marginBottom: 8 }}>
        The measurement hosts submissions ran on. Runtime and energy are only
        comparable within one machine and one energy tool, so each measurement
        records the machine it was produced on. Rows are created automatically
        when a runner reports its hardware descriptor.
      </div>

      {err && <div className="card" style={{ borderColor: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}

      {rows == null
        ? <div className="empty">Loading…</div>
        : rows.length === 0
          ? <div className="empty">No machines recorded yet.</div>
          : (
            <div className="col" style={{ gap: 12 }}>
              {rows.map(m => <MachineCard key={m.id} m={m} />)}
            </div>
          )}
    </div>
  );
}

function MachineCard({ m }: { m: MachinePublic }) {
  return (
    <div className="card col">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <div className="col" style={{ gap: 4 }}>
          <h3 style={{ margin: 0 }}>
            {m.name}
            {m.energy_source && (
              <span className="badge badge-easy"
                    style={{ fontSize: "0.7rem", marginLeft: 8, verticalAlign: "middle" }}>
                {m.energy_source}
              </span>
            )}
          </h3>
          {m.hostname && (
            <div className="muted" style={{ fontSize: "0.85rem" }}><code>{m.hostname}</code></div>
          )}
        </div>
        <div className="row" style={{ gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
          <Mini label="submissions" value={String(m.n_submissions)} />
          {m.cores != null && <Mini label="cores" value={String(m.cores)} />}
        </div>
      </div>

      <div className="row" style={{ gap: 16, flexWrap: "wrap", fontSize: "0.88rem" }}>
        {m.cpu_model && <Field label="CPU" value={m.cpu_model} />}
        {m.os && <Field label="OS" value={m.os} />}
        {m.energy_source && <Field label="Energy tool" value={m.energy_source} />}
      </div>

      {m.notes && (
        <div className="muted" style={{ fontSize: "0.9rem", whiteSpace: "pre-wrap" }}>
          {m.notes}
        </div>
      )}
    </div>
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

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="row" style={{ gap: 6 }}>
      <span className="muted">{label}:</span>
      <span>{value}</span>
    </div>
  );
}
