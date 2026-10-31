#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Build dist/Side_Control-<version>-<arch>.AppImage with quick-sharun (Anylinux AppImages). It bundles
# everything down to glibc, so the AppImage runs on any distribution. Build on Arch Linux with the
# dependencies from the README installed; no root needed.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
BUILD=$ROOT/build/appimage
VERSION=$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' "$ROOT/side_control/__init__.py")
ARCH=$(uname -m)
QUICK_SHARUN=https://raw.githubusercontent.com/pkgforge-dev/Anylinux-AppImages/bacfcc005398d74e6454cdb8f494efe42e0bb60a/useful-tools/quick-sharun.sh

export APPDIR=$BUILD/AppDir
export OUTPATH=$ROOT/dist
export OUTNAME=Side_Control-$VERSION-$ARCH.AppImage
export DESKTOP=$ROOT/packaging/io.github.sidecontrol.SideControl.desktop
export ICON=$ROOT/side_control/data/icons/io.github.sidecontrol.SideControl.svg
export DEPLOY_PYTHON=1 DEPLOY_GTK=1 DEPLOY_GDK=1 DEPLOY_GLYCIN=1
export ALWAYS_SOFTWARE=1  # a settings window: GTK's software renderer is plenty, and no mesa to bundle
export STRACE_MODE=0      # the libraries loaded at runtime are listed below instead

rm -rf "$BUILD"
mkdir -p "$BUILD" "$OUTPATH" "$APPDIR/bin" "$APPDIR/share/side-control"
cd "$BUILD"
curl -fsSL "$QUICK_SHARUN" -o quick-sharun
chmod +x quick-sharun

# the app; AppRun starts bin/<name> when the AppImage is run with that name as its first argument
cp -r "$ROOT/side_control" "$ROOT/side-control" "$ROOT/side-control-backend" "$APPDIR/share/side-control/"
find "$APPDIR/share/side-control" -name __pycache__ -prune -exec rm -rf {} +
for name in side-control side-control-backend; do
    printf '#!/bin/sh\nexec "$APPDIR/bin/python3" "$APPDIR/share/side-control/%s" "$@"\n' "$name" \
        > "$APPDIR/bin/$name"
    chmod +x "$APPDIR/bin/$name"
done

# Python's own C modules, and the libraries that PyGObject opens through typelibs
SITE=$(python3 -c 'import sysconfig; print(sysconfig.get_path("platlib"))')
LIB=/usr/lib
./quick-sharun \
    "$SITE"/gi/*.so "$SITE"/cairo/*.so "$SITE"/_dbus_*.so "$SITE"/evdev/*.so \
    "$LIB"/libgtk-4.so.1 "$LIB"/libadwaita-1.so.0 "$LIB"/libgtk4-layer-shell.so.0 \
    "$LIB"/libgirepository-2.0.so.0 "$LIB"/libgraphene-1.0.so.0 "$LIB"/libpangocairo-1.0.so.0 \
    "$LIB"/libpangoft2-1.0.so.0 "$LIB"/libcairo-gobject.so.2 "$LIB"/libgdk_pixbuf-2.0.so.0 \
    "$LIB"/libglycin-2.so.0 "$LIB"/libharfbuzz.so.0 "$LIB"/libdbus-1.so.3

# quick-sharun copies all of the system's site-packages: keep the four Side Control uses
for site in "$APPDIR"/shared/lib/python3*/site-packages; do
    find "$site" -mindepth 1 -maxdepth 1 \
        ! -name gi ! -name cairo ! -name dbus ! -name evdev ! -name '_dbus_*.so' -exec rm -rf {} +
done

./quick-sharun --make-appimage
echo "Built $OUTPATH/$OUTNAME"
