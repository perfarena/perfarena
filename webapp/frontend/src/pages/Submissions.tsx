import { useEffect, useState } from "react";
import { api, SubmissionPublic } from "../lib/api";
import SubmissionsTable from "../components/SubmissionsTable";

export default function Submissions() {
  const [rows, setRows] = useState<SubmissionPublic[]>([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.submissions().then(setRows).catch((e: unknown) => setErr(String(e)));
  }, []);

  return (
    <div>
      <div className="hero">
        <h1>Submissions</h1>
        <p>Every batch submitted by approved competitors.</p>
      </div>

      {err && <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>}
      <SubmissionsTable rows={rows} />
    </div>
  );
}
