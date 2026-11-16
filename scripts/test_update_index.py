import json
from pathlib import Path
import tempfile
import unittest

from update_index import collect_index, update


def plugin(internal, repository):
    return {
        "InternalName": internal, "Name": internal, "AssemblyVersion": "1.2.3",
        "DalamudApiLevel": 15,
        "RepoUrl": f"https://github.com/pearl-eye/{repository}",
        "DownloadLinkInstall": f"https://github.com/pearl-eye/{repository}/releases/download/v1.2.3/latest.zip",
    }


def source(name):
    return {"name": name, "repository": f"pearl-eye/{name}", "manifestUrl": f"https://example.com/{name}.json"}


class IndexTests(unittest.TestCase):
    def test_multiple_plugins_keep_independent_package_locations(self):
        sources = [source("First"), source("Second")]
        feeds = {sources[0]["manifestUrl"]: [plugin("First", "RepoOne")],
                 sources[1]["manifestUrl"]: [plugin("Second", "RepoTwo")]}
        index = collect_index(sources, feeds.__getitem__)
        self.assertEqual([x["InternalName"] for x in index], ["First", "Second"])
        self.assertIn("/RepoOne/", index[0]["DownloadLinkInstall"])
        self.assertIn("/RepoTwo/", index[1]["DownloadLinkInstall"])

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
                    if fail and url == sources[1]["manifestUrl"]:
                        raise OSError("download unavailable")
                    return [plugin("SamePlugin", "One")]

                with self.assertRaises((ValueError, OSError)):
                    update(registry, output, fetch)
                self.assertEqual(output.read_bytes(), original)

    def test_installation_state_is_not_published(self):
        manifest = plugin("First", "First")
        manifest.update(InstalledFromUrl="old-source", WorkingPluginId="local-id", Disabled=True)
        result = collect_index([source("First")], lambda url: [manifest])[0]
        self.assertNotIn("InstalledFromUrl", result)
        self.assertNotIn("WorkingPluginId", result)
        self.assertNotIn("Disabled", result)
        self.assertEqual(manifest["InstalledFromUrl"], "old-source")


if __name__ == "__main__":
    unittest.main()
