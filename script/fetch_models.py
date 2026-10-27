#!/usr/bin/env python3
"""Prepare integration-test models from the pinned, verified model archive."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import urlsplit
import uuid


PROJECT = Path(__file__).resolve().parents[1]
CATALOG = PROJECT / "catalog/models-v1.json"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_catalog(catalog_path=CATALOG):
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    version = catalog.get("catalogVersion", "")
    files = catalog.get("files", [])
    if (catalog.get("schemaVersion") != 3 or not isinstance(version, str)
            or not re.fullmatch(r"models-v[0-9]+", version)
            or not isinstance(files, list) or not files or len(files) > 512):
        raise ValueError("The committed model catalog is invalid")
    seen = set()
    for item in files:
        path = item["path"]
        validate_relative_path(path)
        validate_metadata(item)
        if path in seen:
            raise ValueError("Duplicate model catalog entry: " + path)
        seen.add(path)
    archive = catalog["archive"]
    validate_metadata(archive)
    if (not isinstance(archive["path"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.tar\.xz", archive["path"])):
        raise ValueError("Invalid model archive name")
    return version, files, archive


def validate_relative_path(path):
    if (not isinstance(path, str) or not path or path.startswith("/") or "\\" in path or ":" in path
            or "\x00" in path or "\n" in path or "\r" in path
            or any(part in ("", ".", "..") for part in path.split("/"))
            or not PurePosixPath(path).parts):
        raise ValueError("Invalid model path: " + str(path))


def validate_metadata(item):
    if not isinstance(item["sourceURL"], str):
        raise ValueError("Invalid model source URL")
    url = urlsplit(item["sourceURL"])
    if (type(item["bytes"]) is not int or not 0 < item["bytes"] <= 2_000_000_000
            or not isinstance(item["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])
            or url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment):
        raise ValueError("Invalid model metadata: " + str(item.get("path")))


def reject_symlink_ancestors(path):
    for current in (path, *path.parents):
        if current.is_symlink():
            raise ValueError("Test model cache must not contain symbolic links: " + str(current))


def safe_destination(root, relative):
    destination = root / relative
    reject_symlink_ancestors(destination)
    return destination


def matches(path, item):
    return (path.is_file() and not path.is_symlink() and path.stat().st_size == item["bytes"]
            and sha256(path) == item["sha256"])


def invalid_cache_files(root, files, jobs):
    def verify(item):
        return matches(safe_destination(root, item["path"]), item)

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return [item["path"] for item, valid in zip(files, pool.map(verify, files)) if not valid]


def extract_verified(archive_path, staging, files):
    expected = {item["path"]: item for item in files}
    seen = set()
    with tarfile.open(archive_path, mode="r:xz", ignore_zeros=True) as archive:
        for member in archive:
            validate_relative_path(member.name)
            if not member.isreg() or member.issparse():
                raise ValueError("Archive entry must be a regular file: " + member.name)
            if member.name not in expected or member.name in seen:
                raise ValueError("Unexpected or duplicate archive entry: " + member.name)
            item = expected[member.name]
            if member.size != item["bytes"]:
                raise ValueError("Archive entry has an invalid size: " + member.name)
            destination = staging / member.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("Unreadable archive entry: " + member.name)
            digest = hashlib.sha256()
            written = 0
            with source, destination.open("xb") as output:
                for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
                    written += len(chunk)
                    if written > item["bytes"]:
                        raise ValueError("Archive entry exceeds its expected size: " + member.name)
                    digest.update(chunk)
                    output.write(chunk)
            if written != item["bytes"] or digest.hexdigest() != item["sha256"]:
                raise ValueError("Extracted model differs from the committed SHA-256: " + member.name)
            seen.add(member.name)
    if seen != expected.keys():
        raise ValueError("Model archive is missing files: " + ", ".join(sorted(expected.keys() - seen)))


def validate_replaceable_cache(root, files):
    """Refuse to replace an unrelated directory supplied through --root."""
    reject_symlink_ancestors(root)
    if not root.exists():
        return
    if not root.is_dir():
        raise ValueError("Test model cache is not a directory: " + str(root))
    expected_files = {item["path"] for item in files}
    expected_directories = {str(parent) for name in expected_files for parent in PurePosixPath(name).parents if str(parent) != "."}
    for directory, directories, names in os.walk(root, followlinks=False):
        for name in directories + names:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or relative not in (expected_directories if path.is_dir() else expected_files):
                raise ValueError("Refusing to replace an unrelated cache entry: " + str(path))


def install_cache(staging, root, files):
    validate_replaceable_cache(root, files)
    backup = root.with_name("." + root.name + ".backup-" + uuid.uuid4().hex)
    try:
        if root.exists():
            os.replace(root, backup)
        os.replace(staging, root)
    except BaseException:
        # Keep the previous cache outside the temporary directory until restored.
        if backup.exists() and not root.exists():
            os.replace(backup, root)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def prepare_archive(root, files, archive, local_archive):
    validate_replaceable_cache(root, files)
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="." + root.name + ".prepare-", dir=root.parent) as temporary:
        working = Path(temporary)
        archive_path = local_archive or working / archive["path"]
        if local_archive is None:
            print("Downloading model archive: " + archive["sourceURL"], flush=True)
            subprocess.run([
                "/usr/bin/curl", "--fail", "--location", "--proto", "=https", "--proto-redir", "=https",
                "--retry", "3", "--connect-timeout", "20", "--max-time", "1200", "--silent", "--show-error",
                "--output", str(archive_path), archive["sourceURL"],
            ], check=True)
        if not matches(archive_path, archive):
            raise ValueError("Model archive differs from the committed size or SHA-256: " + str(archive_path))
        print("Archive verified; extracting and checking all model files", flush=True)
        staging = working / "models"
        staging.mkdir()
        extract_verified(archive_path, staging, files)
        install_cache(staging, root, files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=CATALOG, help="Pinned model catalog")
    parser.add_argument("--check", action="store_true", help="Verify cached models without downloading")
    parser.add_argument("--archive", type=Path, help="Verify and install an existing local model archive")
    parser.add_argument("--root", type=Path, help="Model cache directory (default: build/TestModels/<catalog version>)")
    parser.add_argument("--jobs", type=int, default=4, help="Concurrent cached-file checks (default: 4)")
    args = parser.parse_args()
    if not 1 <= args.jobs <= 16:
        parser.error("--jobs must be between 1 and 16")
    if args.check and args.archive:
        parser.error("--check cannot be combined with --archive")
    version, files, archive = load_catalog(args.catalog)
    root = Path(os.path.abspath(args.root.expanduser())) if args.root else PROJECT / "build/TestModels" / version
    # Canonicalize the user-selected parent (for example macOS /tmp) while still
    # rejecting a cache directory or model file that is itself a symbolic link.
    root = root.parent.resolve() / root.name
    reject_symlink_ancestors(root)
    total = sum(item["bytes"] for item in files)
    print(f"Test models: {len(files)} files, {total:,} bytes\nCache: {root}", flush=True)
    invalid = invalid_cache_files(root, files, args.jobs)
    if args.check and invalid:
        raise ValueError(f"Missing or invalid test models ({len(invalid)}): " + ", ".join(invalid))
    if args.archive or invalid:
        local_archive = args.archive.expanduser().absolute() if args.archive else None
        prepare_archive(root, files, archive, local_archive)
    else:
        print("Reusing the verified local cache; no download needed", flush=True)
    print("All test models match the committed catalog", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, lzma.LZMAError, EOFError, subprocess.CalledProcessError) as error:
        print("Model preparation failed: " + str(error), file=sys.stderr)
        sys.exit(1)
