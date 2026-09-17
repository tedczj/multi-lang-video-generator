#!/bin/bash
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_dir"
exec "$project_dir/.venv/bin/python" -m mlvideo.generate "$@"
