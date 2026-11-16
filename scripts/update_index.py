#!/usr/bin/env python3
"""Collect published plugin manifests into one stable Dalamud repository index."""

import argparse
from fnmatch import fnmatchcase
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import unquote, urlparse
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
    parsed = urlparse(url)
    if parsed.hostname == "api.github.com" and parsed.port in (None, 443):
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if token:
            # urllib does not copy unredirected headers to redirected requests.
            # An API redirect must never forward the token to another host.
            request.add_unredirected_header("Authorization", f"Bearer {token}")
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8-sig"))


def require_repository(repository):
    if not isinstance(repository, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", repository
    ) or repository.split("/")[-1] in (".", ".."):
        raise ValueError("Download statistics repository must be owner/repo")
    return repository


def fetch_releases(repository, fetch=download_json, cache=None):
    """Fetch all release pages once per repository during an index update."""
    repository = require_repository(repository)
    key = repository.casefold()
    if cache is not None and key in cache:
        return cache[key]
    releases = []
    page = 1
    while True:
        response = fetch(
            f"https://api.github.com/repos/{repository}/releases?per_page=100&page={page}"
        )
        if not isinstance(response, list) or any(not isinstance(item, dict) for item in response):
            raise ValueError(f"{repository}: releases API must return a JSON array of releases")
        releases.extend(response)
        if len(response) < 100:
            break
        page += 1
    if cache is not None:
        cache[key] = releases
    return releases


def get_download_count(repository, asset_patterns, fetch=download_json, cache=None, *, exact=False):
    """Count matching ZIP assets across published releases, including prereleases."""
    if not isinstance(asset_patterns, (list, tuple, set)) or not asset_patterns or any(
        not isinstance(pattern, str) or not pattern for pattern in asset_patterns
    ):
        raise ValueError(f"{repository}: assetPatterns must be a nonempty array of strings")
    total = 0
    matched = False
    seen_assets = set()
    for release in fetch_releases(repository, fetch, cache):
        if release.get("draft", False):
            continue
        assets = release.get("assets")
        if not isinstance(assets, list):
            raise ValueError(f"{repository}: release assets must be a JSON array")
        for asset in assets:
            if not isinstance(asset, dict):
                raise ValueError(f"{repository}: release asset must be an object")
            name = asset.get("name")
            if not isinstance(name, str) or not name.lower().endswith(".zip"):
                continue
            selected = name in asset_patterns if exact else any(
                fnmatchcase(name, pattern) for pattern in asset_patterns
            )
            if not selected:
                continue
            count = asset.get("download_count")
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ValueError(f"{repository}: {name} has an invalid download_count")
            matched = True
            # Asset IDs are unique across releases. This also protects against
            # a release moving between pages while an update is in progress.
            asset_id = asset.get("id")
            if asset_id is not None:
                if not isinstance(asset_id, (int, str)) or isinstance(asset_id, bool):
                    raise ValueError(f"{repository}: {name} has an invalid asset ID")
                if asset_id in seen_assets:
                    continue
                seen_assets.add(asset_id)
            total += count
    if not matched:
        raise ValueError(
            f"{repository}: no published ZIP asset matched {sorted(asset_patterns)!r}; "
            "refusing to replace the previous download count with zero"
        )
    return total


def release_asset(url, label):
    """Read owner/repo and one exact asset name from a GitHub release URL."""
    require_https(url, label)
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    if parsed.hostname != "github.com" or parsed.port not in (None, 443) or len(parts) != 6:
        raise ValueError(f"{label}: expected a GitHub release ZIP URL; configure downloadStats otherwise")
    owner, repo, releases, action, target, filename = parts
    if releases != "releases" or not (
        (action == "latest" and target == "download") or (action == "download" and target)
    ):
        raise ValueError(f"{label}: expected a GitHub release ZIP URL")
    repository = require_repository(f"{owner}/{repo}")
    filename = unquote(filename)
    if not filename.lower().endswith(".zip") or any(c in filename for c in "/\\\r\n"):
        raise ValueError(f"{label}: download statistics require a ZIP release asset")
    return repository, filename


def download_overrides(source, entries):
    """Accept one rule for a single plugin, or rules keyed by InternalName."""
    config = source.get("downloadStats")
    if config is None:
        return {}
    if not isinstance(config, dict):
        raise ValueError(f"{source['name']}: downloadStats must be an object")
    names = {
        entry["InternalName"].casefold()
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("InternalName"), str)
    }

    def validate(rule):
        if not isinstance(rule, dict) or set(rule) != {"repository", "assetPatterns"}:
            raise ValueError("Each downloadStats rule needs repository and assetPatterns")
        require_repository(rule["repository"])
        patterns = rule["assetPatterns"]
        if not isinstance(patterns, list) or not patterns or any(
            not isinstance(pattern, str) or not pattern or any(c in pattern for c in "/\\\r\n")
            for pattern in patterns
        ):
            raise ValueError("downloadStats.assetPatterns must be a nonempty array of asset filename patterns")
        return rule

    if "repository" in config or "assetPatterns" in config:
        if len(entries) != 1 or len(names) != 1:
            raise ValueError(f"{source['name']}: a multi-plugin source needs downloadStats keyed by InternalName")
        return {next(iter(names)): validate(config)}
    result = {}
    for internal, rule in config.items():
        if not isinstance(internal, str) or internal.casefold() not in names:
            raise ValueError(f"{source['name']}: downloadStats names an unknown InternalName: {internal}")
        key = internal.casefold()
        if key in result:
            raise ValueError(f"{source['name']}: duplicate downloadStats InternalName: {internal}")
        result[key] = validate(rule)
    return result


def plugin_download_count(item, override, fetch, cache):
    if override is not None:
        return get_download_count(
            override["repository"], override["assetPatterns"], fetch, cache
        )
    repositories = {}
    for field in ("DownloadLinkInstall", "DownloadLinkUpdate", "DownloadLinkTesting"):
        if not item.get(field):
            continue
        repository, asset = release_asset(item[field], f"{item['InternalName']}.{field}")
        key = repository.casefold()
        if key not in repositories:
            repositories[key] = (repository, set())
        repositories[key][1].add(asset)
    return sum(
        get_download_count(repository, assets, fetch, cache, exact=True)
        for repository, assets in repositories.values()
    )


def upstream_versions(source, fetch, cache):
    """Read optional upstream versions without normalizing their version strings."""
    if "upstreamManifestUrl" not in source:
        return None
    url = source["upstreamManifestUrl"]
    require_https(url, "upstreamManifestUrl")
    if url in cache:
        return cache[url]
    document = fetch(url)
    if isinstance(document, dict):
        entries = [document]
    elif isinstance(document, list):
        entries = document
    else:
        raise ValueError(f"{source['name']}: upstream manifest must be an object or JSON array")
    versions = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"{source['name']}: upstream plugin manifest must be an object")
        internal = entry.get("InternalName")
        if not isinstance(internal, str) or not internal.strip() or any(c in internal for c in "/\\\r\n"):
            raise ValueError(f"{source['name']}: invalid upstream InternalName")
        key = internal.casefold()
        if key in versions:
            raise ValueError(f"Duplicate upstream InternalName: {internal}")
        versions[key] = entry.get("AssemblyVersion")
    cache[url] = versions
    return versions


def collect_index(sources, fetch=download_json):
    if not isinstance(sources, list):
        raise ValueError("sources.json must contain an array")
    collected = {}
    release_cache = {}
    upstream_cache = {}
    for source in sources:
        if not isinstance(source, dict) or not source.get("name") or not source.get("repository"):
            raise ValueError("Each source needs name, repository, and manifestUrl")
        url = source.get("manifestUrl")
        require_https(url, "manifestUrl")
        entries = fetch(url)
        if not isinstance(entries, list):
            raise ValueError(f"{source['name']}: manifest must be a JSON array")
        versions = upstream_versions(source, fetch, upstream_cache)
        overrides = download_overrides(source, entries)
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
            if versions is not None:
                key = internal.casefold()
                if key not in versions:
                    raise ValueError(f"{internal}: plugin is missing from the upstream manifest")
                upstream_version = versions[key]
                if not isinstance(upstream_version, str) or not re.fullmatch(r"\d+(?:\.\d+){1,3}", upstream_version):
                    raise ValueError(f"{internal}: invalid upstream AssemblyVersion")
                if version != upstream_version:
                    raise ValueError(
                        f"{internal}: AssemblyVersion {version} differs from upstream {upstream_version}; "
                        "versions must match exactly"
                    )
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
            item["DownloadCount"] = plugin_download_count(item, overrides.get(key), fetch, release_cache)
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
