import { Link } from "react-router-dom";

/* Compact prompt-template cell for tables. Renders the registered prompt's
 * name as a link to its detail page; falls back to a muted "ad-hoc" tag
 * when the submission used an inline prompt (prompt_id is null).
 *
 * Used by every submissions / rankings table so a viewer can tell at a
 * glance which prompt produced which row — and click through to compare
 * the prompts side-by-side. */
export default function PromptName({
  id, name, compact = true,
}: { id: number | null; name: string | null; compact?: boolean }) {
  if (!id || !name) {
    return (
      <span className="muted" title="ad-hoc inline prompt (not from the registry)"
            style={{ fontSize: compact ? "0.8rem" : undefined }}>
        ad-hoc
      </span>
    );
  }
  return (
    <Link to={`/prompts/${id}`} title={`registered prompt: ${name}`}
          style={{ fontSize: compact ? "0.85rem" : undefined }}>
      <code>{name}</code>
    </Link>
  );
}
