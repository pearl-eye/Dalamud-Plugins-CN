import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from io import BytesIO
from urllib.request import HTTPRedirectHandler

from update_index import collect_index, download_json, get_download_count, update


def plugin(internal, repository):
    return {
        "InternalName": internal, "Name": internal, "AssemblyVersion": "1.2.3",
        "DalamudApiLevel": 15,
        "RepoUrl": f"https://github.com/pearl-eye/{repository}",
        "DownloadLinkInstall": f"https://github.com/pearl-eye/{repository}/releases/download/v1.2.3/latest.zip",
    }


def source(name):
    return {"name": name, "repository": f"pearl-eye/{name}", "manifestUrl": f"https://example.com/{name}.json"}


def release(assets, **kwargs):
    return {"draft": False, "assets": assets, **kwargs}


def asset(name="latest.zip", count=0, identifier=1):
    return {"name": name, "download_count": count, "id": identifier}


def release_url(repository, page=1):
    return f"https://api.github.com/repos/{repository}/releases?per_page=100&page={page}"


class IndexTests(unittest.TestCase):
    def test_multiple_plugins_keep_independent_package_locations(self):
        sources = [source("First"), source("Second")]
        feeds = {sources[0]["manifestUrl"]: [plugin("First", "RepoOne")],
                 sources[1]["manifestUrl"]: [plugin("Second", "RepoTwo")],
                 release_url("pearl-eye/RepoOne"): [release([asset(count=7)])],
                 release_url("pearl-eye/RepoTwo"): [release([asset(count=19)])]}
        index = collect_index(sources, feeds.__getitem__)
        self.assertEqual([x["InternalName"] for x in index], ["First", "Second"])
        self.assertIn("/RepoOne/", index[0]["DownloadLinkInstall"])
        self.assertIn("/RepoTwo/", index[1]["DownloadLinkInstall"])
        self.assertEqual([x["DownloadCount"] for x in index], [7, 19])

    def test_duplicate_or_failed_source_never_replaces_existing_index(self):
        sources = [source("First"), source("Second")]
        for fail in (False, True):
            with self.subTest(failed_download=fail), tempfile.TemporaryDirectory() as folder:
                registry = Path(folder) / "sources.json"
                output = Path(folder) / "repo.json"
                registry.write_text(json.dumps(sources))
                output.write_text('[{"existing":"preserve me"}]\n')
                original = output.read_bytes()

                def fetch(url):
                    if url.startswith("https://api.github.com/"):
                        return [release([asset(count=5)])]
                    if fail and url == sources[1]["manifestUrl"]:
                        raise OSError("download unavailable")
                    return [plugin("SamePlugin", "One")]

                with self.assertRaises((ValueError, OSError)):
                    update(registry, output, fetch)
                self.assertEqual(output.read_bytes(), original)

    def test_installation_state_is_not_published(self):
        manifest = plugin("First", "First")
        manifest.update(InstalledFromUrl="old-source", WorkingPluginId="local-id", Disabled=True)
        result = collect_index([source("First")], lambda url:
                               [release([asset()])] if url.startswith("https://api.github.com/") else [manifest])[0]
        self.assertNotIn("InstalledFromUrl", result)
        self.assertNotIn("WorkingPluginId", result)
        self.assertNotIn("Disabled", result)
        self.assertEqual(manifest["InstalledFromUrl"], "old-source")

    def test_historical_and_prerelease_downloads_exclude_metadata_drafts_and_duplicates(self):
        item = plugin("First", "First")
        item["DownloadLinkTesting"] = item["DownloadLinkInstall"]
        feeds = {
            source("First")["manifestUrl"]: [item],
            release_url("pearl-eye/First"): [
                release([asset(count=2), asset("repo.json", 100, 2), asset("source.zip", 90, 3)]),
                release([asset(count=5, identifier=4)], prerelease=True),
                release([asset(count=50, identifier=5)], draft=True),
                release([asset(count=2)]),
            ],
        }
        result = collect_index([source("First")], feeds.__getitem__)
        self.assertEqual(result[0]["DownloadCount"], 7)

    def test_shared_repository_keeps_plugin_counts_separate_and_caches_pages(self):
        first, second = plugin("First", "Packages"), plugin("Second", "Packages")
        first["DownloadLinkInstall"] = first["DownloadLinkInstall"].replace("latest.zip", "First.zip")
        second["DownloadLinkInstall"] = second["DownloadLinkInstall"].replace("latest.zip", "Second.zip")
        calls = []

        def fetch(url):
            calls.append(url)
            if url == source("Collection")["manifestUrl"]:
                return [first, second]
            return [release([asset("First.zip", 11), asset("Second.zip", 23, 2)])]

        result = collect_index([source("Collection")], fetch)
        self.assertEqual([x["DownloadCount"] for x in result], [11, 23])
        self.assertEqual(calls.count(release_url("pearl-eye/Packages")), 1)

    def test_pagination_is_complete(self):
        pages = {
            release_url("pearl-eye/First"): [release([asset(count=1, identifier=i)]) for i in range(100)],
            release_url("pearl-eye/First", 2): [release([asset(count=7, identifier=101)])],
        }
        self.assertEqual(get_download_count("pearl-eye/First", ["latest.zip"], pages.__getitem__), 107)

    def test_versioned_asset_overrides_are_per_plugin(self):
        registry = source("Collection")
        registry["downloadStats"] = {
            "First": {"repository": "pearl-eye/Packages", "assetPatterns": ["First-v*.zip"]},
            "Second": {"repository": "pearl-eye/Packages", "assetPatterns": ["Second-v*.zip"]},
        }
        feeds = {
            registry["manifestUrl"]: [plugin("First", "First"), plugin("Second", "Second")],
            release_url("pearl-eye/Packages"): [release([
                asset("First-v1.zip", 5), asset("First-v2.zip", 7, 2), asset("Second-v1.zip", 30, 3)
            ])],
        }
        result = collect_index([registry], feeds.__getitem__)
        self.assertEqual([x["DownloadCount"] for x in result], [12, 30])

    def test_no_matching_package_or_api_failure_preserves_index(self):
        for response in ([release([asset("repo.json", 50)])], OSError("GitHub API unavailable")):
            with self.subTest(response=response), tempfile.TemporaryDirectory() as folder:
                registry, output = Path(folder) / "sources.json", Path(folder) / "repo.json"
                registry.write_text(json.dumps([source("First")]))
                output.write_text('[{"DownloadCount":88}]\n')
                original = output.read_bytes()

                def fetch(url):
                    if not url.startswith("https://api.github.com/"):
                        return [plugin("First", "First")]
                    if isinstance(response, Exception):
                        raise response
                    return response

                with self.assertRaises((ValueError, OSError)):
                    update(registry, output, fetch)
                self.assertEqual(output.read_bytes(), original)

    def test_real_zero_is_valid_and_overlapping_patterns_do_not_double_count(self):
        self.assertEqual(get_download_count("pearl-eye/First", ["latest.zip"],
                                           lambda url: [release([asset(count=0)])]), 0)
        self.assertEqual(get_download_count("pearl-eye/First", ["First-*.zip", "First-v2.zip"],
                                           lambda url: [release([asset("First-v2.zip", 9)])]), 9)

    def test_token_is_only_sent_to_github_api_and_not_forwarded_on_redirect(self):
        requests = []

        def open_request(request, timeout):
            requests.append(request)
            return BytesIO(b"[]")

        with patch.dict("os.environ", {"GH_TOKEN": "not-a-real-token"}), patch("update_index.urlopen", open_request):
            download_json("https://api.github.com/repos/pearl-eye/First/releases")
            download_json("https://github.com/pearl-eye/First/releases/latest/download/repo.json")
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer not-a-real-token")
        self.assertIsNone(requests[1].get_header("Authorization"))
        redirected = HTTPRedirectHandler().redirect_request(
            requests[0], None, 302, "Found", {}, "https://example.com/redirected"
        )
        self.assertIsNone(redirected.get_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
