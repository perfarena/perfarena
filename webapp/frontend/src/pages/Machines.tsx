import { useEffect, useState } from "react";
import { api, MachinePublic } from "../lib/api";

/* /machines — the physical/virtual boxes submissions were measured on. Energy
 * (and runtime) are only comparable within ONE machine + one energy tool, so
 * the machine is part of every measurement's provenance. Rows are created
 * automatically when a runner reports its descriptor during dispatch; an admin
 * can then edit the metadata (real CPU model, core count, notes) inline. */
export default function Machines() {
  const [rows, setRows] = useState<MachinePublic[] | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.machines().then(setRows).catch((e: unknown) => setErr(String(e)));
    // me() 401s when signed out — treat any failure as "not admin".
    api.me().then(me => setIsAdmin(me.role === "admin")).catch(() => setIsAdmin(false));
  }, []);

  function onSaved(updated: MachinePublic) {
    setRows(rs => (rs ?? []).map(r => (r.id === updated.id ? updated : r)));
  }

  return (
    <div className="col">
      <h1 style={{ marginBottom: 4 }}>Machines</h1>
      <div className="muted" style={{ fontSize: "0.9rem", marginBottom: 8 }}>
        The measurement hosts submissions ran on. Runtime and energy are only
        comparable within one machine and one energy tool, so each measurement
        records the machine it was produced on. Rows are created automatically
        when a runner reports its hardware descriptor.
        {isAdmin && " As an admin you can edit each machine's details below."}
      </div>

      {err && <div className="card" style={{ borderColor: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}

      {rows == null
        ? <div className="empty">Loading…</div>
        : rows.length === 0
          ? <div className="empty">No machines recorded yet.</div>
          : (
            <div className="col" style={{ gap: 12 }}>
              {rows.map(m => (
                <MachineCard key={m.id} m={m} isAdmin={isAdmin} onSaved={onSaved} />
              ))}
            </div>
          )}
    </div>
  );
}

function MachineCard({
  m, isAdmin, onSaved,
}: {
  m: MachinePublic; isAdmin: boolean; onSaved: (u: MachinePublic) => void;
}) {
  const [editing, setEditing] = useState(false);
  if (editing) {
    return (
      <MachineEditForm
        m={m}
        onCancel={() => setEditing(false)}
        onSaved={u => { onSaved(u); setEditing(false); }}
      />
    );
  }
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
          {isAdmin && (
            <button className="secondary" onClick={() => setEditing(true)}>Edit</button>
          )}
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

/* Inline edit form (admin only). Mirrors the Machine fields the backend
 * accepts. `cores` is the only numeric field — parsed to an int on save,
 * cleared to null when the box is empty. */
function MachineEditForm({
  m, onCancel, onSaved,
}: {
  m: MachinePublic; onCancel: () => void; onSaved: (u: MachinePublic) => void;
}) {
  const [name, setName] = useState(m.name);
  const [hostname, setHostname] = useState(m.hostname ?? "");
  const [cpuModel, setCpuModel] = useState(m.cpu_model ?? "");
  const [cores, setCores] = useState(m.cores != null ? String(m.cores) : "");
  const [energySource, setEnergySource] = useState(m.energy_source ?? "");
  const [os, setOs] = useState(m.os ?? "");
  const [notes, setNotes] = useState(m.notes ?? "");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  async function save() {
    setErr(""); setSaving(true);
    try {
      const updated = await api.updateMachine(m.id, {
        name: name.trim(),
        hostname: hostname.trim() || null,
        cpu_model: cpuModel.trim() || null,
        cores: cores.trim() === "" ? null : Number(cores),
        energy_source: energySource.trim() || null,
        os: os.trim() || null,
        notes: notes.trim() || null,
      });
      onSaved(updated);
    } catch (e: unknown) {
      setErr(String(e));
    } finally { setSaving(false); }
  }

  return (
    <div className="card col" style={{ gap: 10 }}>
      <h3 style={{ margin: 0 }}>Edit machine #{m.id}</h3>
      <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
        <EditField label="name">
          <input value={name} onChange={e => setName(e.target.value)} />
        </EditField>
        <EditField label="hostname">
          <input value={hostname} onChange={e => setHostname(e.target.value)} />
        </EditField>
        <EditField label="cores">
          <input type="number" min={0} value={cores}
                 onChange={e => setCores(e.target.value)} style={{ width: 100 }} />
        </EditField>
        <EditField label="energy tool">
          <input value={energySource} placeholder="rapl | codecarbon | powermetrics | none"
                 onChange={e => setEnergySource(e.target.value)} />
        </EditField>
        <EditField label="OS">
          <input value={os} onChange={e => setOs(e.target.value)} />
        </EditField>
      </div>
      <EditField label="CPU model">
        <input value={cpuModel} onChange={e => setCpuModel(e.target.value)}
               style={{ minWidth: 320 }} />
      </EditField>
      <EditField label="notes">
        <textarea value={notes} onChange={e => setNotes(e.target.value)}
                  rows={3} style={{ minWidth: 320, width: "100%" }} />
      </EditField>

      {err && <div className="muted" style={{ color: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}

      <div className="row" style={{ gap: 8 }}>
        <button onClick={save} disabled={saving || name.trim() === ""}>
          {saving ? "Saving…" : "Save"}
        </button>
        <button className="secondary" onClick={onCancel} disabled={saving}>Cancel</button>
      </div>
    </div>
  );
}

function EditField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="col" style={{ gap: 4 }}>
      <span className="muted" style={{ fontSize: "0.72rem", textTransform: "uppercase", letterSpacing: 0.5 }}>
        {label}
      </span>
      {children}
    </label>
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
