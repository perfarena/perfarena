#!/usr/bin/env bash
# Deploy the new harness files and handler to bicho, then restart its runner.
#
#   ./deploy_bicho.sh            # check, back up, copy, restart
#   ./deploy_bicho.sh --check    # only compare the files with manifest.json
#   ./deploy_bicho.sh --rollback # restore the files of the latest backup, restart
#
# The files come from this repository checkout (paths in manifest.json, from
# the repository root; Energy-Languages is the submodule). Each local file must
# match its md5 in manifest.json, so pull and update the submodule first. On
# bicho, each file must hold either the version recorded as bicho_before_md5
# (it is then replaced) or the new version (already deployed). Any other
# content means that bicho changed since the manifest was made: the script stops
# and copies nothing. The old files go to ~/harness_backup_<time>.tgz on bicho
# first. SSH asks for the password once (a shared connection); set SSHPASS_FILE
# to use sshpass instead.
set -euo pipefail
HOST="${BICHO_HOST:-nyuad@10.228.128.30}"
REMOTE="${BICHO_ROOT:-/home/nyuad/bench/perfarena-leetcode}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
CM="$HOME/.ssh/cm-perfarena-%r@%h:%p"
SSH_OPTS=(-o ControlMaster=auto -o "ControlPath=$CM" -o ControlPersist=15m -o StrictHostKeyChecking=accept-new)
if [ -n "${SSHPASS_FILE:-}" ]; then SSH=(sshpass -f "$SSHPASS_FILE" ssh); SCP=(sshpass -f "$SSHPASS_FILE" scp)
else SSH=(ssh); SCP=(scp); fi
rssh() { "${SSH[@]}" "${SSH_OPTS[@]}" "$HOST" "$@"; }
rscp() { "${SCP[@]}" "${SSH_OPTS[@]}" "$@"; }

# (no `mapfile`: macOS ships bash 3.2; the paths have no spaces)
FILES=($(python3 -c "import json; print(' '.join(sorted(json.load(open('$HERE/manifest.json')))))"))

if [ "${1:-}" = "--rollback" ]; then
  rssh "set -e; b=\$(ls -t ~/harness_backup_*.tgz | head -1); echo \"restoring \$b\"; tar -xzf \"\$b\" -C '$REMOTE'"
  rscp "$HERE/restart_runner.py" "$HOST:/tmp/restart_runner.py"
  rssh "python3 /tmp/restart_runner.py"
  exit 0
fi

echo "== comparing the local files with manifest.json"
for f in "${FILES[@]}"; do
  want=$(python3 -c "import json; print(json.load(open('$HERE/manifest.json'))['$f']['md5'])")
  have=$(python3 -c "import hashlib; print(hashlib.md5(open('$REPO/$f', 'rb').read()).hexdigest())" 2>/dev/null || true)
  [ "$have" = "$want" ] || { echo "  $f: local file differs from manifest.json (git pull && git submodule update --init?)"; exit 1; }
done
echo "== comparing bicho's files with manifest.json"
status=0
for f in "${FILES[@]}"; do
  have=$(rssh "md5sum '$REMOTE/$f' 2>/dev/null | cut -c1-32 || true")
  read -r new before < <(python3 -c "import json; m=json.load(open('$HERE/manifest.json'))['$f']; print(m['md5'], m['bicho_before_md5'])")
  if [ "$have" = "$new" ]; then echo "  $f: already deployed"
  elif [ "$have" = "$before" ] || { [ -z "$have" ] && [ "$before" = absent ]; }; then echo "  $f: will be replaced"
  else echo "  $f: CHANGED ON BICHO ($have), not the expected $before"; status=1; fi
done
[ "$status" = 0 ] || { echo "stopping: bicho differs from what this bundle expects"; exit 1; }
[ "${1:-}" = "--check" ] && exit 0

echo "== backing up and copying"
stamp=$(date +%Y%m%d-%H%M%S)
existing=$(for f in "${FILES[@]}"; do echo "$f"; done | tr '\n' ' ')
rssh "cd '$REMOTE' && tar -czf ~/harness_backup_$stamp.tgz \$(for f in $existing; do [ -e \"\$f\" ] && echo \"\$f\"; done) && echo \"  backup ~/harness_backup_$stamp.tgz\""
for f in "${FILES[@]}"; do
  rscp "$REPO/$f" "$HOST:$REMOTE/$f"
done
rssh "cd '$REMOTE' && md5sum $existing" | while read -r sum path; do
  want=$(python3 -c "import json; print(json.load(open('$HERE/manifest.json'))['$path']['md5'])")
  [ "$sum" = "$want" ] && echo "  $path ok" || { echo "  $path WRONG after copy"; exit 1; }
done

echo "== restarting the runner"
rscp "$HERE/restart_runner.py" "$HOST:/tmp/restart_runner.py"
rssh "python3 /tmp/restart_runner.py"
echo "done. Next: python3 remeasure.py --check (see README.md)"
