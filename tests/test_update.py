# SPDX-License-Identifier: GPL-3.0-or-later
"""Checking GitHub releases and updating the AppImage: python -m unittest discover tests"""
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from side_control.app import update  # noqa: E402


RELEASE = {
    "html_url": "https://github.com/Rakura-cloud/side-control/releases/tag/v0.2.0",
    "assets": [
        {"name": "Side_Control-0.2.0-aarch64.AppImage", "size": 2,
         "browser_download_url": "https://example.org/arm"},
        {"name": "Side_Control-0.2.0-x86_64.AppImage", "size": 8,
         "browser_download_url": "https://example.org/app"},
        {"name": "Side_Control-0.2.0-x86_64.AppImage.zsync", "size": 1,
         "browser_download_url": "https://example.org/app.zsync"}]}


class FakeOpener:
    """Stands in for urllib.request.urlopen: answers with the bytes keyed by URL, records what was
    asked for, and raises OSError for the rest, like being offline does."""

    def __init__(self, files):
        self.files = files
        self.asked = []

    def __call__(self, request):
        self.asked.append(request.full_url)
        if request.full_url not in self.files:
            raise OSError("no route to host")
        return io.BytesIO(self.files[request.full_url])


def fake_release(data, checksum_text=None, checksum_name=None):
    """A release carrying `data` as its AppImage (https://example.org/app) and a checksum asset."""
    assets = [{"name": "Side_Control-0.2.0-x86_64.AppImage", "size": len(data),
               "browser_download_url": "https://example.org/app"}]
    files = {"https://example.org/app": data}
    if checksum_name:
        assets.append({"name": "Side_Control-0.2.0-x86_64.AppImage" + checksum_name,
                       "size": len(checksum_text),
                       "browser_download_url": "https://example.org/app" + checksum_name})
        files["https://example.org/app" + checksum_name] = checksum_text.encode()
    return {"html_url": RELEASE["html_url"], "assets": assets}, FakeOpener(files)


class Versions(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(update.parse_version("0.2.0"), (0, 2, 0, None))
        self.assertEqual(update.parse_version("0.2.0-dev.abc123"), (0, 2, 0, "abc123"))
        self.assertEqual(update.parse_version("v0.2.0"), (0, 2, 0, None))
        self.assertIsNone(update.parse_version("0.2"))
        self.assertIsNone(update.parse_version("latest"))
        self.assertIsNone(update.parse_version(None))

    def test_stable_and_dev_compare(self):
        """A dev version is a pre-release of its base: 0.2.0-dev.abc < 0.2.0 < 0.2.1."""
        for candidate, running in (("0.2.0", "0.1.9"), ("0.2.1", "0.2.0"), ("0.2.0", "0.2.0-dev.abc"),
                                   ("0.2.0-dev.abc", "0.1.0"), ("1.0.0", "0.9.9-dev.abc")):
            self.assertTrue(update.is_newer(candidate, running), f"{candidate} over {running}")
        for candidate, running in (("0.1.9", "0.2.0"), ("0.2.0", "0.2.0"), ("0.2.0-dev.abc", "0.2.0"),
                                   ("0.1.0-dev.abc", "0.2.0")):
            self.assertFalse(update.is_newer(candidate, running), f"{candidate} over {running}")

    def test_dev_channel_follows_the_sha(self):
        """The dev tag rolls: any other sha of the same version is the newer one."""
        self.assertTrue(update.is_newer("0.2.0-dev.bbb", "0.2.0-dev.aaa"))
        self.assertFalse(update.is_newer("0.2.0-dev.aaa", "0.2.0-dev.aaa"))
        self.assertFalse(update.is_newer("0.1.0-dev.bbb", "0.2.0-dev.aaa"))

    def test_bad_versions_are_never_newer(self):
        self.assertFalse(update.is_newer("abc", "0.1.0"))
        self.assertFalse(update.is_newer("0.2.0", ""))

    def test_channel(self):
        self.assertEqual(update.channel("0.2.0"), "stable")
        self.assertEqual(update.channel("0.2.0-dev.abc"), "dev")


class Releases(unittest.TestCase):
    def test_fetches_the_chosen_channel_s_release(self):
        for chosen, tail in (("stable", "/latest"), ("dev", "/tags/dev")):
            opener = FakeOpener({update.RELEASES_URL + tail: json.dumps(RELEASE).encode()})
            self.assertEqual(update.fetch_release(chosen, opener)["html_url"], RELEASE["html_url"])
            self.assertEqual(opener.asked, [update.RELEASES_URL + tail])

    def test_find_update_follows_the_chosen_channel(self):
        for chosen, tail in (("stable", "/latest"), ("dev", "/tags/dev")):
            opener = FakeOpener({update.RELEASES_URL + tail: json.dumps(RELEASE).encode()})
            self.assertEqual(update.find_update(chosen, "0.1.0", opener).version, "0.2.0")
            self.assertEqual(opener.asked, [update.RELEASES_URL + tail])

    def test_picks_the_appimage_and_its_checksum(self):
        asset, checksum = update.pick_assets(RELEASE["assets"])
        self.assertEqual(asset["name"], "Side_Control-0.2.0-x86_64.AppImage")
        self.assertEqual(checksum["name"], "Side_Control-0.2.0-x86_64.AppImage.zsync")
        # a .sha256 beats a .zsync when the release has both
        both = RELEASE["assets"] + [{"name": "Side_Control-0.2.0-x86_64.AppImage.sha256", "size": 1,
                                     "browser_download_url": "https://example.org/app.sha256"}]
        self.assertEqual(update.pick_assets(both)[1]["name"], "Side_Control-0.2.0-x86_64.AppImage.sha256")
        self.assertEqual(update.pick_assets(RELEASE["assets"][:1]), (None, None))  # only aarch64
        self.assertEqual(update.pick_assets([]), (None, None))

    def test_reads_the_version_out_of_the_asset_name(self):
        self.assertEqual(update.asset_version("Side_Control-0.2.0-x86_64.AppImage"), "0.2.0")
        self.assertEqual(update.asset_version("Side_Control-0.2.0-dev.abc123-x86_64.AppImage"),
                         "0.2.0-dev.abc123")
        self.assertIsNone(update.asset_version("Side_Control-0.2.0-x86_64.AppImage.zsync"))

    def test_check_release(self):
        found = update.check_release(RELEASE, "0.1.0", "stable")
        self.assertEqual(found.version, "0.2.0")
        self.assertEqual(found.url, RELEASE["html_url"])
        self.assertEqual(found.asset["name"], "Side_Control-0.2.0-x86_64.AppImage")
        self.assertIsNone(update.check_release(RELEASE, "0.2.0", "stable"))  # the same is not newer
        self.assertIsNone(update.check_release(RELEASE, "0.3.0", "stable"))
        self.assertIsNone(update.check_release({"assets": []}, "0.1.0", "stable"))

    def test_check_release_on_the_dev_channel(self):
        dev, _opener = fake_release(b"12345678")
        dev["assets"][0]["name"] = "Side_Control-0.2.0-dev.bbb-x86_64.AppImage"
        self.assertEqual(update.check_release(dev, "0.2.0-dev.aaa", "dev").version, "0.2.0-dev.bbb")
        self.assertIsNone(update.check_release(dev, "0.2.0-dev.bbb", "dev"))
        self.assertIsNone(update.check_release(dev, "0.3.0-dev.aaa", "dev"))  # a newer base stays newer

    def test_check_release_offers_the_other_channel_to_switch_to(self):
        """The chosen channel's release is offered even when it is not newer, so a dev build can
        switch back to stable (and the other way round)."""
        self.assertEqual(update.check_release(RELEASE, "0.3.0-dev.abc", "stable").version, "0.2.0")
        dev, _opener = fake_release(b"12345678")
        dev["assets"][0]["name"] = "Side_Control-0.1.0-dev.abc-x86_64.AppImage"
        self.assertEqual(update.check_release(dev, "0.2.0", "dev").version, "0.1.0-dev.abc")
        self.assertIsNone(update.check_release(dev, "0.2.0-dev.abc", "dev"))  # its own channel: not newer


class Download(unittest.TestCase):
    def test_downloads_in_chunks_and_checks_the_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Side_Control-0.2.0-x86_64.AppImage")
            opener = FakeOpener({"https://example.org/app": b"new file"})
            seen = []
            self.assertEqual(update.download("https://example.org/app", path, 8, opener, seen.append), 8)
            with open(path, "rb") as f:
                self.assertEqual(f.read(), b"new file")
            self.assertTrue(os.access(path, os.X_OK))  # it steps into a program's place
            self.assertEqual(seen, [1])
            with self.assertRaises(update.UpdateError):  # the size is what the release says, or nothing
                update.download("https://example.org/app", path, 99, opener)

    def test_reads_checksums(self):
        self.assertEqual(update.checksum_of(f"{'a' * 64}  app\n", "app.sha256"), ("sha256", "a" * 64))
        self.assertIsNone(update.checksum_of("nothing here", "app.sha256"))
        self.assertEqual(update.checksum_of(f"zsync: 0.6.2\nSHA-1: {'b' * 40}\n", "app.zsync"),
                         ("sha1", "b" * 40))
        self.assertIsNone(update.checksum_of("zsync: 0.6.2\n", "app.zsync"))

    def test_install_verifies_and_keeps_the_old_file(self):
        data = b"the new one"
        text = f"{hashlib.sha256(data).hexdigest()}  app\n"
        for checksum_name, checksum_text in ((".sha256", text),
                                             (".zsync", f"zsync: 0.6.2\nSHA-1: {hashlib.sha1(data).hexdigest()}\n"),
                                             (None, None)):  # no checksum in the release: the size must do
            with self.subTest(checksum=checksum_name), tempfile.TemporaryDirectory() as tmp:
                release, opener = fake_release(data, checksum_text, checksum_name)
                path = os.path.join(tmp, "Side_Control-0.2.0-x86_64.AppImage")
                with open(path, "wb") as f:
                    f.write(b"the old one")
                update.install(update.check_release(release, "0.1.0", "stable"), path, opener)
                with open(path, "rb") as f:
                    self.assertEqual(f.read(), data)
                with open(path + ".old", "rb") as f:  # kept until the new one has started once
                    self.assertEqual(f.read(), b"the old one")
                self.assertFalse(os.path.exists(path + ".new"))
                update.discard_old(path)
                self.assertFalse(os.path.exists(path + ".old"))

    def test_install_refuses_a_corrupt_download(self):
        data = b"the new one"
        for checksum_name, checksum_text in ((".sha256", f"{'0' * 64}  app\n"),
                                             (".zsync", f"zsync: 0.6.2\nSHA-1: {'0' * 40}\n")):
            with self.subTest(checksum=checksum_name), tempfile.TemporaryDirectory() as tmp:
                release, opener = fake_release(data, checksum_text, checksum_name)
                path = os.path.join(tmp, "Side_Control-0.2.0-x86_64.AppImage")
                with open(path, "wb") as f:
                    f.write(b"the old one")
                with self.assertRaises(update.UpdateError):
                    update.install(update.check_release(release, "0.1.0", "stable"), path, opener)
                with open(path, "rb") as f:
                    self.assertEqual(f.read(), b"the old one")  # the running file is left alone
                self.assertFalse(os.path.exists(path + ".new"))

    def test_replace_lays_the_previous_old_aside(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "app")
            for name, text in (("app", "two"), ("app.old", "one"), ("app.new", "three")):
                with open(os.path.join(tmp, name), "w") as f:
                    f.write(text)
            update.replace(path)
            with open(path) as f:
                self.assertEqual(f.read(), "three")
            with open(path + ".old") as f:
                self.assertEqual(f.read(), "two")
            self.assertFalse(os.path.exists(path + ".new"))


if __name__ == "__main__":
    unittest.main()
