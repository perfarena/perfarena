/* Static snapshot mode: the GitHub Pages build (VITE_STATIC=1).
 *
 * The snapshot has no backend. backend/app/static_export.py answers every public
 * GET request that the pages can send and writes each answer to
 * data/<key>.json, where <key> is the start of the SHA-256 of the canonical
 * request: the decoded path and the sorted query pairs, as JSON. staticKey()
 * computes the same key, so a page reads the file that the export wrote for its
 * request. A request that the export did not answer gets a 404. */

export const STATIC = import.meta.env.VITE_STATIC === "1";

/** Where the live arena runs, for links from the snapshot. */
export const LIVE_URL = "https://perfarena.net";

export const NOT_IN_SNAPSHOT =
  `This view is not in the snapshot. The live arena at ${LIVE_URL} has every view.`;

function canonical(path: string): string {
  const u = new URL(path, "http://snapshot.invalid");
  const pairs = [...u.searchParams.entries()].sort((a, b) =>
    a[0] !== b[0] ? (a[0] < b[0] ? -1 : 1) : a[1] === b[1] ? 0 : a[1] < b[1] ? -1 : 1);
  return JSON.stringify([decodeURIComponent(u.pathname), pairs]);
}

export async function staticKey(path: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical(path)));
  return [...new Uint8Array(digest)].slice(0, 12)
    .map(b => b.toString(16).padStart(2, "0")).join("");
}

/** The snapshot's answer to a GET request, as fetch() would give it. */
export async function staticFetch(path: string): Promise<Response> {
  return fetch(`${import.meta.env.BASE_URL}data/${await staticKey(path)}.json`);
}

export interface SnapshotMeta { exported_at: string; requests: number; }

/** When the export ran, for the banner. */
export async function snapshotMeta(): Promise<SnapshotMeta | null> {
  try {
    const res = await fetch(`${import.meta.env.BASE_URL}data/meta.json`);
    return res.ok ? (await res.json()) as SnapshotMeta : null;
  } catch {
    return null;
  }
}
