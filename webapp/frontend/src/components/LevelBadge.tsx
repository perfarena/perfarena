export default function LevelBadge({ level }: { level: string }) {
  const cls = level === "Easy" ? "badge-easy" : level === "Medium" ? "badge-medium" : "badge-hard";
  return <span className={`badge ${cls}`}>{level}</span>;
}
