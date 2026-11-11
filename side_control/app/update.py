# SPDX-License-Identifier: GPL-3.0-or-later
"""Checking GitHub releases for a newer Side Control, and taking the update.

The pure functions take what they work on and an `opener` instead of reaching for the network
themselves, so tests can give them a fake release and a fake download. A version is "X.Y.Z" (stable)
or "X.Y.Z-dev.<sha>" (the rolling dev channel, released under the `dev` tag, with the sha it was
built from in the asset names). The channel to check is the one the user chose in Settings ("Dev
Builds"), not the one the running build came from. The mixin turns what the check finds into the
window's banner and runs the download; the app checks at start and then once a day.
"""
import collections
import hashlib
import json
import os
import platform
import re
import sys
import threading
import urllib.request

from gi.repository import Adw, Gio, GLib

from .. import VERSION
from .config import APPIMAGE, RELEASES_PAGE, RELEASES_URL, SERVICE


DAY = 24 * 60 * 60  # seconds between update checks: news on the day it comes out, never a nag
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "Side-Control"}  # the API insists
VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:-dev\.(\w+))?")
CHUNK = 65536  # bytes per read while downloading


class UpdateError(Exception):
    """What arrived is not what the release promised."""


class Update(collections.namedtuple("Update", "version url asset checksum")):
    """What a release offers over the running version: its number, where its notes are, its AppImage
    asset and the asset carrying its checksum (.sha256 or .zsync), as the GitHub API reports them."""


# --- versions ---

def parse_version(text):
    """(major, minor, patch, dev sha or None) from "0.2.0" or "0.2.0-dev.abc123"; None if it is
    not a version."""
    match = VERSION_RE.fullmatch((text or "").removeprefix("v"))
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3)), match.group(4)


def version_key(version):
    """Sorts parsed versions. A dev version is a pre-release of its base, so it sorts first."""
    major, minor, patch, sha = version
    return major, minor, patch, 0 if sha else 1


def is_newer(candidate, running):
    """True if the `candidate` version outruns the `running` one. On the dev channel (both dev) the
    tag rolls, so any other sha of the same version counts as newer."""
    c, r = parse_version(candidate), parse_version(running)
    if c is None or r is None:
        return False
    if version_key(c) != version_key(r):
        return version_key(c) > version_key(r)
    return bool(c[3]) and bool(r[3]) and c[3] != r[3]


def channel(version):
    """"dev" for X.Y.Z-dev.<sha> (rolling, from the `dev` tag), "stable" otherwise."""
    parsed = parse_version(version)
    return "dev" if parsed and parsed[3] else "stable"


# --- releases ---

def asset_version(name, suffix="-x86_64.AppImage"):
    """The version an AppImage asset is built from: "Side_Control-0.2.0-dev.abc-x86_64.AppImage"
    gives "0.2.0-dev.abc"."""
    match = re.search(r"(\d+\.\d+\.\d+(?:-dev\.\w+)?)" + re.escape(suffix) + "$", name or "")
    return match.group(1) if match else None


def pick_assets(assets, suffix="-x86_64.AppImage"):
    """The release's AppImage for machines like this one, and the asset carrying its checksum (a
    .sha256 or .zsync), if the release has one."""
    appimage = next((a for a in assets if a.get("name", "").endswith(suffix)), None)
    checksum = None
    if appimage:
        checksum = (next((a for a in assets if a.get("name") == appimage["name"] + ".sha256"), None)
                    or next((a for a in assets if a.get("name") == appimage["name"] + ".zsync"), None))
    return appimage, checksum


def check_release(release, running, chosen, suffix="-x86_64.AppImage"):
    """What `release` offers over the running version: an Update, or None when there is nothing to
    take. The `chosen` channel's release is always worth offering while the running build came from
    the other one (switching back), and only when it is newer within one channel."""
    asset, checksum = pick_assets(release.get("assets") or [], suffix)
    version = asset_version(asset["name"], suffix) if asset else None
    if version is None or (chosen == channel(running) and not is_newer(version, running)):
        return None
    return Update(version, release.get("html_url") or RELEASES_PAGE, asset, checksum)


def fetch_release(chosen, opener=urllib.request.urlopen):
    """The GitHub release the `chosen` channel ("dev" or "stable") publishes: the dev tag's, or the
    latest one."""
    path = "/tags/dev" if chosen == "dev" else "/latest"
    with opener(urllib.request.Request(RELEASES_URL + path, headers=HEADERS)) as src:
        return json.load(src)


def fetch_text(url, opener=urllib.request.urlopen):
    with opener(urllib.request.Request(url, headers=HEADERS)) as src:
        return src.read().decode("utf-8", errors="replace")


def find_update(chosen, version=VERSION, opener=urllib.request.urlopen):
    """The update GitHub has for this Side Control on the `chosen` channel, or None."""
    return check_release(fetch_release(chosen, opener), version, chosen,
                         f"-{platform.machine()}.AppImage")


# --- downloading and replacing the AppImage ---

def download(url, path, size=None, opener=urllib.request.urlopen, progress=None):
    """Stream `url` to `path` and make it executable, calling progress(fraction) on the way.
    Raises UpdateError when what arrived is not the `size` bytes the release promised."""
    written = 0
    with opener(urllib.request.Request(url, headers=HEADERS)) as src, open(path, "wb") as out:
        while chunk := src.read(CHUNK):
            out.write(chunk)
            written += len(chunk)
            if progress and size:
                progress(min(written / size, 1))
    os.chmod(path, 0o755)  # it steps into a program's place
    if size and written != size:
        raise UpdateError(f"the download is {written} bytes, the release says {size}")
    return written


def checksum_of(text, name):
    """The whole-file hash a checksum asset asks for: ("sha256"|"sha1", hexdigest), or None. A
    .sha256 asset is one sha256sum line; a .zsync asset names its hash in a header line."""
    if name.endswith(".sha256"):
        digest = (text.split() or [""])[0].lower()
        return ("sha256", digest) if re.fullmatch("[0-9a-f]{64}", digest) else None
    for line in text.splitlines():
        match = re.fullmatch(r"(SHA-256|SHA-1): *([0-9a-fA-F]+)", line.strip())
        if match:
            return ("sha256" if match.group(1) == "SHA-256" else "sha1", match.group(2).lower())
    return None


def hash_file(path, algo):
    digest = hashlib.new(algo)
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def discard(path):
    """Remove `path` if it is there: a download that did not work out, or the file an update
    replaced."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def discard_old(path):
    """Remove <path>.old: the new file has started once, the one it replaced can go."""
    discard(path + ".old")


def replace(path):
    """Move the download at <path>.new over `path`, keeping the file it replaces as <path>.old
    until the new one has started once (discard_old)."""
    discard(path + ".old")
    if os.path.exists(path):
        os.rename(path, path + ".old")
    os.rename(path + ".new", path)


def install(update, path, opener=urllib.request.urlopen, progress=None):
    """Download `update`'s AppImage next to `path` and put it in place. Raises UpdateError and
    leaves `path` alone when the download does not match the release's size or checksum."""
    try:
        download(update.asset["browser_download_url"], path + ".new", update.asset.get("size"),
                 opener, progress)
        if update.checksum:
            expected = checksum_of(fetch_text(update.checksum["browser_download_url"], opener),
                                   update.checksum["name"])
            if expected and hash_file(path + ".new", expected[0]) != expected[1]:
                raise UpdateError("the download does not match the release's checksum")
    except Exception:
        discard(path + ".new")
        raise
    replace(path)


class UpdateMixin:
    """The update banner and what its button does; part of Window."""

    def start_update_checks(self):
        """Look for updates now and once a day; the old AppImage can go, this one has started."""
        self.update, self.update_state = None, None
        if APPIMAGE:
            discard_old(APPIMAGE)
        GLib.timeout_add_seconds(DAY, self.check_updates)
        self.check_updates()

    def check_updates(self):
        """Once a day, and when "Check for Updates" comes on: look now, if the checks are on."""
        if self.settings["check_updates"]:
            self.check_updates_now()
        return GLib.SOURCE_CONTINUE

    def check_updates_now(self):
        """Look on the chosen channel right away. Flipping "Dev Builds" comes here even while the
        once-a-day checks are off: the user just asked, and the release to switch to should show up
        at once."""
        chosen = "dev" if self.settings["dev_builds"] else "stable"
        threading.Thread(target=self._check_updates, args=(chosen,), daemon=True).start()

    def _check_updates(self, chosen):
        try:
            found = find_update(chosen)
        except (OSError, ValueError) as err:
            print(f"update check: {err}", file=sys.stderr)  # offline is nothing to make a fuss about
            return
        GLib.idle_add(self.show_update, found)

    def show_update(self, update):
        """The banner: what is new, where its notes are, and the button to take it (or git pull)."""
        if self.update_state in ("downloading", "installed"):  # an update is under way or waiting
            return GLib.SOURCE_REMOVE                         # for its restart: leave the banner be
        if update is None:  # the chosen channel has nothing to offer (Dev Builds was flipped)
            self.update_banner.set_revealed(False)
            return GLib.SOURCE_REMOVE
        self.update, self.update_state = update, "found"
        notes = f'<a href="{GLib.markup_escape_text(update.url, -1)}">Release notes</a>'
        if APPIMAGE:
            self.update_banner.set_title(f"Update available: {update.version} — {notes}")
            self.update_banner.set_button_label("Update")
        else:  # a git checkout is updated with git; the app only says there is something new
            self.update_banner.set_title(f"Update available: {update.version} — run git pull "
                                         f"to update — {notes}")
            self.update_banner.set_button_label(None)
        self.update_banner.set_revealed(True)
        return GLib.SOURCE_REMOVE

    def on_update_button(self, _banner):
        if self.update_state == "installed":
            self.restart_update()
        else:
            self.download_update()

    def download_update(self):
        self.update_state = "downloading"
        self.update_banner.set_button_label(None)
        threading.Thread(target=self._download_update, daemon=True).start()

    def _download_update(self):
        try:
            install(self.update, APPIMAGE,
                    progress=lambda fraction: GLib.idle_add(self.download_progress, fraction))
        except (OSError, ValueError, UpdateError) as err:
            GLib.idle_add(self.download_failed, err)
            return
        GLib.idle_add(self.download_done)

    def download_progress(self, fraction):
        self.update_banner.set_title(f"Downloading update… {round(100 * fraction)} %")
        return GLib.SOURCE_REMOVE

    def download_done(self):
        self.update_state = "installed"
        self.update_banner.set_title(f"Side Control {self.update.version} is ready — restart to use it")
        self.update_banner.set_button_label("Restart now")
        return GLib.SOURCE_REMOVE

    def download_failed(self, err):
        self.update_state = "found"
        self.update_banner.set_title(f"Could not update: {err}")
        self.update_banner.set_button_label("Try Again")
        return GLib.SOURCE_REMOVE

    def restart_update(self, _banner=None):
        """Start again from the new file. The backend stops first, so the fresh app starts it again
        from that file too (start_service writes what the service runs before each start)."""
        self.restarting = True
        self.backend.send(cmd="quit")  # in case the service cannot be stopped below
        try:
            proc = Gio.Subprocess.new(["systemctl", "stop", "--no-ask-password", SERVICE],
                                      Gio.SubprocessFlags.STDERR_PIPE)
        except GLib.Error:
            self.exec_update()
            return
        proc.communicate_utf8_async(None, None, lambda *_args: self.exec_update())

    def exec_update(self):
        try:
            os.execv(APPIMAGE, [APPIMAGE, *sys.argv[1:]])
        except OSError as err:
            self.restarting = False
            self.toasts.add_toast(Adw.Toast(title=f"Could not restart: {err}"))
