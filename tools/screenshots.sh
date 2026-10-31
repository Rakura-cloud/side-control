#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Run tools/screenshots.py in a headless mutter, so it needs no display: tools/screenshots.sh [output dir]
set -eu
here=$(dirname "$0")
display=side-control-shots-$$
mutter --headless --wayland --no-x11 --virtual-monitor 1200x800 --wayland-display "$display" >/dev/null 2>&1 &
mutter=$!
trap 'kill $mutter 2>/dev/null' EXIT
for _ in $(seq 50); do
    [ -S "${XDG_RUNTIME_DIR:?}/$display" ] && break
    sleep 0.1
done
WAYLAND_DISPLAY=$display python "$here/screenshots.py" "$@"
