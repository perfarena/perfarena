type SortDir = "asc" | "desc";

/**
 * Table header cell. Optionally:
 *  - `tip`  → an on-hover (and on-focus) explanation tooltip.
 *  - `sortKey` + `onSort` → makes the whole header clickable to sort by that
 *    column; shows a ▲/▼ marker when it's the active sort column.
 * The two are independent — a header can be tooltip-only, sort-only, or both.
 */
export default function Th({
  label, tip, sortKey, activeKey, dir, onSort,
}: {
  label: string;
  tip?: string;
  sortKey?: string;
  activeKey?: string;
  dir?: SortDir;
  onSort?: (key: string) => void;
}) {
  const sortable = !!sortKey && !!onSort;
  const active = sortable && activeKey === sortKey;
  const marker = active ? (dir === "asc" ? " ▲" : " ▼") : "";

  const inner = tip
    ? <span className="th-tip" tabIndex={0} data-tip={tip}>{label}</span>
    : <span>{label}</span>;

  if (!sortable) return <th>{inner}</th>;

  return (
    <th
      onClick={() => onSort!(sortKey!)}
      onKeyDown={e => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSort!(sortKey!); }
      }}
      tabIndex={0}
      role="columnheader"
      aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}
      title={active ? "" : "Click to sort"}
      style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}
    >
      {inner}
      <span className="muted" style={{ fontSize: "0.8em" }}>
        {marker || (active ? "" : " ↕")}
      </span>
    </th>
  );
}
