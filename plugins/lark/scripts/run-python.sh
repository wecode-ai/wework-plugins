#!/bin/sh
set -eu
if command -v python3 >/dev/null 2>&1; then
    exec python3 "$@"
fi
if command -v python >/dev/null 2>&1; then
    exec python "$@"
fi
echo 'Python 3.9 or newer is required for this plugin.' >&2
exit 2
