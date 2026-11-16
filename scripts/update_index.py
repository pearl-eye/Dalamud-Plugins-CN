#!/usr/bin/env python3
"""Collect published plugin manifests into one stable Dalamud repository index."""

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
LOCAL_FIELDS = {
    "Disabled", "Testing", "ScheduledForDeletion", "WorkingPluginId",
    "InstalledFromUrl", "IsThirdParty", "SourceRepo",
}


def require_https(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an HTTPS URL")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(f"{label} must be an HTTPS URL without embedded credentials")


def download_json(url):
    require_https(url, "manifestUrl")
    request = Request(url, headers={"User-Agent": "Dalamud-Plugins-CN-index/1.0"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8-sig"))


def collect_index(sources, fetch=download_json):
    if not isinstance(sources, list):
        raise ValueError("sources.json must contain an array")
    collected = {}
    for source in sources:
        if not isinstance(source, dict) or not source.get("name") or not source.get("repository"):
            raise ValueError("Each source needs name, repository, and manifestUrl")
        url = source.get("manifestUrl")
        require_https(url, "manifestUrl")
        entries = fetch(url)
        if not isinstance(entries, list):
            raise ValueError(f"{source['name']}: manifest must be a JSON array")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError(f"{source['name']}: plugin manifest must be an object")
            item = {key: value for key, value in entry.items() if key not in LOCAL_FIELDS}
            internal = item.get("InternalName")
            if not isinstance(internal, str) or not internal.strip() or any(c in internal for c in "/\\\r\n"):
                raise ValueError(f"{source['name']}: invalid InternalName")
            if not isinstance(item.get("Name"), str) or not item["Name"].strip():
                raise ValueError(f"{internal}: missing display Name")
            version = item.get("AssemblyVersion")
            if not isinstance(version, str) or not re.fullmatch(r"\d+(?:\.\d+){1,3}", version):
                raise ValueError(f"{internal}: invalid stable AssemblyVersion")
            api = item.get("DalamudApiLevel")
            if not isinstance(api, int) or isinstance(api, bool) or api < 1:
                raise ValueError(f"{internal}: invalid DalamudApiLevel")
            require_https(item.get("DownloadLinkInstall"), f"{internal}.DownloadLinkInstall")
            item.setdefault("DownloadLinkUpdate", item["DownloadLinkInstall"])
            require_https(item["DownloadLinkUpdate"], f"{internal}.DownloadLinkUpdate")
            if item.get("DownloadLinkTesting"):
                require_https(item["DownloadLinkTesting"], f"{internal}.DownloadLinkTesting")
            key = internal.casefold()
            if key in collected:
                raise ValueError(f"Duplicate InternalName: {internal}; keep one source per plugin")
            collected[key] = item
    return [collected[key] for key in sorted(collected)]


def update(sources_path, output_path, fetch=download_json, check=False):
    sources = json.loads(Path(sources_path).read_text(encoding="utf-8-sig"))
    index = collect_index(sources, fetch)
    rendered = json.dumps(index, ensure_ascii=False, indent=2) + "\n"
    output = Path(output_path)
    current = output.read_text(encoding="utf-8") if output.exists() else None
    if current == rendered:
        print(f"Index already current: {len(index)} plugin(s)")
        return 0
    if check:
        print("Index needs updating", file=sys.stderr)
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(rendered)
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    print(f"Updated {output}: {len(index)} plugin(s)")
    for item in index:
        print(f"  {item['Name']} {item['AssemblyVersion']} (API {item['DalamudApiLevel']})")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=ROOT / "sources.json")
    parser.add_argument("--output", type=Path, default=ROOT / "repo.json")
    parser.add_argument("--check", action="store_true", help="Report stale index without modifying it")
    args = parser.parse_args()
    try:
        return update(args.sources, args.output, check=args.check)
    except Exception as error:
        print(f"Index update failed; existing index was preserved: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
