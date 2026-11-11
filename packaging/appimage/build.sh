#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Build dist/Side_Control-<version>-<arch>.AppImage with quick-sharun (Anylinux AppImages). It bundles
# everything down to glibc, so the AppImage runs on any distribution. Build on Arch Linux with the
# dependencies from the README installed; no root needed.
#
# CHANNEL=stable (the default) builds a release, version VERSION from side_control/__init__.py.
# CHANNEL=dev builds for the dev pre-release, version VERSION-dev.<short commit>; COMMIT gives the
# commit (the workflow passes it, so git isn't needed there), else it is the checkout's HEAD.
# In GitHub Actions the AppImage gets update information for its channel's release, from
# GITHUB_REPOSITORY; a local build gets none.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
BUILD=$ROOT/build/appimage
CHANNEL=${CHANNEL:-stable}
VERSION=$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' "$ROOT/side_control/__init__.py")
ARCH=$(uname -m)
QUICK_SHARUN=https://raw.githubusercontent.com/pkgforge-dev/Anylinux-AppImages/bacfcc005398d74e6454cdb8f494efe42e0bb60a/useful-tools/quick-sharun.sh

# the zsync patterns: GitHub's "latest" is never a pre-release, so a stable AppImage only updates to
# stable releases, and a dev one only to the dev pre-release
case $CHANNEL in
    stable)
        RELEASE=latest ZSYNC="*-$ARCH.AppImage.zsync"
        ;;
    dev)
        COMMIT=${COMMIT:-$(git -C "$ROOT" rev-parse HEAD)}
        VERSION=$VERSION-dev.$(printf %s "$COMMIT" | cut -c1-7)
        RELEASE=dev ZSYNC="*-dev.*-$ARCH.AppImage.zsync"
        ;;
    *)
        echo "CHANNEL must be stable or dev, not $CHANNEL" >&2
        exit 1
        ;;
esac

# appimagetool writes VERSION into the desktop entry (X-AppImage-Version) and UPINFO into the AppImage
export VERSION
if [ -n "${GITHUB_REPOSITORY:-}" ]; then
    export UPINFO="gh-releases-zsync|${GITHUB_REPOSITORY%/*}|${GITHUB_REPOSITORY#*/}|$RELEASE|$ZSYNC"
fi
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
echo "$VERSION" > "$APPDIR/share/side-control/side_control/version"  # read by side_control/__init__.py
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
