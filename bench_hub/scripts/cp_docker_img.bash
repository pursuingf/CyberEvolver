#!/usr/bin/env bash
# File name: docker-ctf-transfer.sh
set -euo pipefail

# ====== Configuration ======
JUMP_HOST="${JUMP_HOST:?Set JUMP_HOST, for example user@jump-host}"
DEST_HOST="${DEST_HOST:?Set DEST_HOST, for example user@destination-host}"
DEST_DIR="${DEST_DIR:?Set DEST_DIR on the destination host}"
KEY="${KEY:-${SSH_KEY:-}}"
: "${KEY:?Set KEY or SSH_KEY to the private key path}"
THREADS="${THREADS:-20}"
# ===========================

# 1. Load private key.
eval "$(ssh-agent -s)" >/dev/null
ssh-add "$KEY" >/dev/null

# 2. Generate task list.
mapfile -t IMGS < <(docker images --format='{{.Repository}}:{{.Tag}}' | grep ctf)

# 3. Define per-image worker.
do_one(){
  local img=$1
  local tmpfile="/tmp/${img//[\/:]_/-}.tar.gz"
  echo "[$$] Starting $img"
  docker save "$img" | gzip > "$tmpfile"
  scp -A -o ProxyJump="$JUMP_HOST" \
      "$tmpfile" "${DEST_HOST}:${DEST_DIR}/" && rm -f "$tmpfile"
  echo "[$$] Finished $img"
}
export -f do_one
export JUMP_HOST DEST_HOST DEST_DIR

# 4. Run workers in parallel.
parallel --progress --jobs "$THREADS" do_one ::: "${IMGS[@]}"
         

echo "All images transferred."
