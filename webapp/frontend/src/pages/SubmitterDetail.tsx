import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, SubmitterDetail } from "../lib/api";
import SubmissionsTable from "../components/SubmissionsTable";

export default function SubmitterDetailPage() {
  const { slug = "" } = useParams();
  const [s, setS] = useState<SubmitterDetail | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.submitterDetail(slug).then(setS).catch((e: unknown) => setErr(String(e)));
  }, [slug]);

  if (err) return <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>;
  if (!s) return <div className="empty">Please wait.</div>;

  return (
    <div className="col">
      <div className="hero">
        <h1>{s.display_name}</h1>
        <p>
          {s.org ? <>organization · </> : <>single competitor · </>}
          {s.n_submissions} submissions across {s.n_models} model(s) ·
          languages: {s.languages.join(", ") || "–"}
        </p>
      </div>

      <h2>All submissions</h2>
      <SubmissionsTable rows={s.submissions} />
    </div>
  );
}
