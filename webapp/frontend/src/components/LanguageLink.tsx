import { Link } from "react-router-dom";

export default function LanguageLink({ name }: { name: string }) {
  return <Link to={`/languages/${encodeURIComponent(name)}`}>{name}</Link>;
}
