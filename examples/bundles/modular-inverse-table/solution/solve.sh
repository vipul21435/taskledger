#!/usr/bin/env bash
# Reference solution entry point. TASK_WORKDIR is /app inside the environment image.
set -euo pipefail
exec "${PYTHON:-python3}" "$(dirname "$0")/solve.py" "${TASK_WORKDIR:-/app}"
