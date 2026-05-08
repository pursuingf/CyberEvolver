#!/bin/sh
set -eu

NESTED_IMAGE="llmctf/2018f-msc-showdown-container:latest"

if ! docker image inspect "${NESTED_IMAGE}" >/dev/null 2>&1; then
  docker build -t "${NESTED_IMAGE}" /showdown_container
fi

exec python3 /serve.py
