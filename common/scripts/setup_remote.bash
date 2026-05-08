#!/bin/bash
# Save as fwd.sh, then run chmod +x fwd.sh.
set -euo pipefail

START="${START:-30000}"
END="${END:-32999}"
SSH_USER="${SSH_USER:?Set SSH_USER for the remote account}"
JUMP_HOST="${JUMP_HOST:?Set JUMP_HOST for the SSH jump host}"
DEST_HOST="${DEST_HOST:?Set DEST_HOST for the destination host}"

for ((p=START; p<=END; p++)); do
  ssh -fNT -o ExitOnForwardFailure=yes \
      -J "${SSH_USER}@${JUMP_HOST}" \
      -L "${p}:${DEST_HOST}:${p}" \
      "${SSH_USER}@${DEST_HOST}"
done
