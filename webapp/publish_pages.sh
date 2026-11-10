#!/usr/bin/env bash
# Build the read-only snapshot of PerfArena and commit it to the local gh-pages
# branch, for GitHub Pages.
#
#   webapp/publish_pages.sh <database | export folder> [base]
#
#   <database>  the live arena.db, or a copy of it. The export copies it again
#               and never writes it.
#   <export folder>
#               a folder that static_export.py already wrote (it holds
#               data/meta.json), for example on the server:
#                 docker compose exec backend python -m app.static_export \
#                   --db /data/arena.db --out /data/pages-export
#               then copied here. Step 1 is then skipped.
#   [base]      the path that GitHub Pages serves the site under:
#               /perfarena/ for https://perfarena.github.io/perfarena/
#               (the default), or / for a custom domain.
#
# Steps:
#   1. backend/app/static_export.py answers every public GET request that the
#      snapshot pages can send and writes the answers to data/.
#   2. The frontend is built in static mode (VITE_STATIC=1), which reads data/
#      instead of the API.
#   3. The build and data/ become one commit on gh-pages, with no parent. Each
#      snapshot replaces the previous one, so the branch does not grow with every
#      snapshot.
#
# The script does not push. To preview the site first:
#   cd <the printed folder> && python3 -m http.server 8080
# and open http://localhost:8080<base>. Then publish with:
#   git push --force perfarena gh-pages
#
# Run it with the environment of the live server, because some settings change
# what the public pages show (for example ARENA_LEADERBOARD_GATE). PYTHON selects
# the interpreter (default: backend/.venv/bin/python).
set -euo pipefail

SRC=${1:?usage: publish_pages.sh <database | export folder> [base]}
BASE=${2:-/perfarena/}
[[ "$BASE" == /*/ || "$BASE" == / ]] || { echo "base must start and end with /" >&2; exit 2; }
SRC=$(cd "$(dirname "$SRC")" && pwd)/$(basename "$SRC")
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(git -C "$HERE" rev-parse --show-toplevel)
OUT=${OUT:-$(mktemp -d -t perfarena-pages)}
PY=${PYTHON:-$HERE/backend/.venv/bin/python}

if [[ -f "$SRC/data/meta.json" ]]; then
  echo "== 1/3 use the export in $SRC"
  EXPORT=$SRC
else
  echo "== 1/3 export the public API answers"
  EXPORT=$OUT/export
  (cd "$HERE/backend" && "$PY" -m app.static_export --db "$SRC" --out "$EXPORT")
fi

echo "== 2/3 build the frontend in static mode (base $BASE)"
(cd "$HERE/frontend" && VITE_STATIC=1 VITE_BASE="$BASE" npm run build -- --outDir "$OUT/site" --emptyOutDir)
cp -R "$EXPORT/data" "$OUT/site/data"
# GitHub Pages answers a path with no file (an app route such as /rankings)
# with 404.html, so 404.html is the app too.
cp "$OUT/site/index.html" "$OUT/site/404.html"
touch "$OUT/site/.nojekyll"                    # serve the files as they are

echo "== 3/3 commit to gh-pages"
# The site becomes one commit with no parent, built with a separate index, so the
# checkout and the index of the repository stay as they are.
GIT_DIR=$(git -C "$REPO" rev-parse --absolute-git-dir)
export GIT_INDEX_FILE="$OUT/index"
(cd "$OUT/site" && git --git-dir="$GIT_DIR" --work-tree=. add -A -f .)
TREE=$(git --git-dir="$GIT_DIR" write-tree)
COMMIT=$(git --git-dir="$GIT_DIR" commit-tree "$TREE" -m "Snapshot of the arena, $(date -u +%Y-%m-%d)")
git --git-dir="$GIT_DIR" update-ref refs/heads/gh-pages "$COMMIT"
unset GIT_INDEX_FILE

PREVIEW=$OUT/preview
mkdir -p "$PREVIEW$(dirname "${BASE%/}")"
[[ "$BASE" == / ]] && PREVIEW=$OUT/site || ln -sfn "$OUT/site" "$PREVIEW${BASE%/}"
echo
echo "gh-pages is now $COMMIT ($(du -sh "$OUT/site" | cut -f1))."
echo "Preview: (cd $PREVIEW && python3 -m http.server 8080), then open http://localhost:8080$BASE"
echo "         (the preview server has no 404.html fallback, so start from that page)"
echo "Publish: git push --force perfarena gh-pages"
