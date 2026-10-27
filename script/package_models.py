#!/usr/bin/env python3
"""Build the reproducible model archive from an already verified model cache."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tarfile

PROJECT = Path(__file__).resolve().parents[1]
CATALOG = PROJECT / "catalog/models-v1.json"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_metadata(path, release):
    return {
        "path": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "sourceURL": f"https://github.com/SteveTanSaMa/Waifu2x-Desktop-Models/releases/download/{release}/{path.name}",
    }


def verify_sources(root, files):
    for item in files:
        relative = item["path"]
        if (relative.startswith("/") or "\\" in relative or ":" in relative
                or any(p in ("", ".", "..") for p in relative.split("/"))):
            raise ValueError("Invalid model path: " + relative)
        path = root / relative
        if not path.is_file() or path.is_symlink() or path.stat().st_size != item["bytes"] or sha256(path) != item["sha256"]:
            raise ValueError("Missing or invalid source model: " + str(path))
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Source model escapes the cache: " + str(path))


def package_repairs(root, files, version, release, output_dir):
    """One compressed payload per content hash; initial installs keep one archive."""
    verify_sources(root, files)
    if not re.fullmatch(r"models-v\d+", version) or release != version:
        raise ValueError("Invalid model version or release tag")
    output_dir.mkdir(parents=True, exist_ok=True)
    assets = {}
    entries = []
    for item in sorted(files, key=lambda item: item["path"]):
        digest = item["sha256"]
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid model checksum")
        if digest not in assets:
            destination = output_dir / f"Waifu2x-model-{digest}.tar.xz"
            partial = destination.with_suffix(destination.suffix + ".partial")
            try:
                with tarfile.open(partial, mode="w:xz", format=tarfile.USTAR_FORMAT, preset=6) as archive:
                    member = tarfile.TarInfo("model")
                    member.size, member.mode = item["bytes"], 0o644
                    member.uid = member.gid = member.mtime = 0
                    with (root / item["path"]).open("rb") as source:
                        archive.addfile(member, source)
                os.replace(partial, destination)
            finally:
                partial.unlink(missing_ok=True)
            assets[digest] = archive_metadata(destination, release)
        entries.append({key: item[key] for key in ("path", "bytes", "sha256")} | {"archive": assets[digest]})
    result = dict(schemaVersion=1, catalogVersion=version, files=entries)
    destination = output_dir / f"Waifu2x-{version}-repair.json"
    partial = destination.with_suffix(".json.partial")
    try:
        partial.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)
    print(f"Model repair index: {len(files)} paths, {len(assets)} unique compressed payloads", flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="Verified model cache root")
    parser.add_argument("--catalog", type=Path, default=CATALOG, help="Pinned model catalog to package")
    parser.add_argument("--update-catalog", action="store_true", help="Record archive metadata in the catalog")
    parser.add_argument("--repair-only", action="store_true", help="Generate repair assets without repacking the full archive")
    parser.add_argument("--output-dir", type=Path, default=PROJECT / "dist")
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text())
    version = catalog["catalogVersion"]
    if not re.fullmatch(r"models-v\d+", version):
        raise ValueError("Invalid model cache version")
    root = args.root or PROJECT / "build/TestModels" / version
    files = catalog["files"]
    verify_sources(root, files)
    package_repairs(root, files, version, version, args.output_dir)
    catalog["repairManifestURL"] = f"https://github.com/SteveTanSaMa/Waifu2x-Desktop-Models/releases/download/{version}/Waifu2x-{version}-repair.json"
    if args.repair_only:
        if args.update_catalog:
            args.catalog.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
        return
    destination = args.output_dir / ("Waifu2x-" + version + ".tar.xz")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    print(f"Packing {len(files)} verified files ({sum(i['bytes'] for i in files):,} bytes)", flush=True)
    try:
        with tarfile.open(temporary, mode="w:xz", format=tarfile.USTAR_FORMAT, preset=6) as archive:
            for item in sorted(files, key=lambda i: i["path"]):
                info = tarfile.TarInfo(item["path"])
                info.size = item["bytes"]
                info.mode = 0o644
                info.mtime = info.uid = info.gid = 0
                with (root / item["path"]).open("rb") as stream:
                    archive.addfile(info, stream)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    metadata = archive_metadata(destination, version)
    if args.update_catalog:
        catalog["schemaVersion"] = 3
        catalog["archive"] = metadata
        args.catalog.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(metadata, indent=2), flush=True)


if __name__ == "__main__":
    main()
