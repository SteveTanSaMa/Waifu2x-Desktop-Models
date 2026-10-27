#!/usr/bin/env python3
"""Verify a legacy v1.0.3 release and stage the identical models-v1 assets."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tarfile

from package_models import sha256

PROJECT = Path(__file__).resolve().parents[1]
OLD_BASE = 'https://github.com/SteveTanSaMa/Waifu2x-Desktop/releases/download/v1.0.3/'
NEW_BASE = 'https://github.com/SteveTanSaMa/Waifu2x-Desktop-Models/releases/download/models-v1/'


def stage(source, destination):
    if destination.exists():
        raise ValueError(f'Refusing to replace {destination}')
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path, help='Downloaded legacy release assets')
    parser.add_argument('--output', type=Path, default=PROJECT / 'dist')
    args = parser.parse_args()
    catalog = json.loads((PROJECT / 'catalog/models-v1.json').read_text())
    manifest_name = 'Waifu2x-models-v1-repair.json'
    archive_name = catalog['archive']['path']
    sums = {}
    for line in (args.source / 'SHA256SUMS').read_text().splitlines():
        match = re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9._-]+)', line)
        if not match or match[2] in sums:
            raise ValueError('Invalid or duplicate legacy checksum line')
        sums[match[2]] = match[1]
    def verify(name, expected=None):
        path = args.source / name
        digest = sha256(path)
        if name not in sums or digest != sums[name] or (expected and digest != expected):
            raise ValueError(f'Checksum mismatch: {name}')
        return path
    archive = verify(archive_name, catalog['archive']['sha256'])
    if archive.stat().st_size != catalog['archive']['bytes']:
        raise ValueError('Full archive size mismatch')
    manifest_path = verify(manifest_name)
    manifest = json.loads(manifest_path.read_text())
    files = {f['path']: f for f in catalog['files']}
    if manifest['schemaVersion'] != 1 or manifest['catalogVersion'] != 'models-v1' or len(manifest['files']) != len(files):
        raise ValueError('Invalid legacy manifest')
    seen, assets = set(), {}
    for entry in manifest['files']:
        name = entry['path']
        if name in seen or name not in files or any(entry[k] != files[name][k] for k in ('bytes', 'sha256')):
            raise ValueError(f'Invalid model entry: {name}')
        seen.add(name)
        item = entry['archive']
        filename = 'Waifu2x-model-' + entry['sha256'] + '.tar.xz'
        if item['path'] != filename or item['sourceURL'] != OLD_BASE + filename:
            raise ValueError(f'Invalid repair source: {name}')
        if filename in assets and assets[filename] != item:
            raise ValueError(f'Inconsistent repair metadata: {name}')
        assets[filename] = item
    for filename, item in assets.items():
        path = verify(filename, item['sha256'])
        if path.stat().st_size != item['bytes']:
            raise ValueError(f'Invalid repair size: {filename}')
        with tarfile.open(path, 'r:xz') as tar:
            members = tar.getmembers()
            if len(members) != 1 or not members[0].isfile() or members[0].name != 'model':
                raise ValueError(f'Invalid repair archive: {filename}')
            digest = hashlib.sha256()
            with tar.extractfile(members[0]) as stream:
                for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
                    digest.update(chunk)
            if digest.hexdigest() != filename[len('Waifu2x-model-'):-len('.tar.xz')]:
                raise ValueError(f'Invalid repair model bytes: {filename}')
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError('Output directory must be empty')
    args.output.mkdir(parents=True, exist_ok=True)
    for name in [archive_name, *sorted(assets)]:
        stage(args.source / name, args.output / name)
    for entry in manifest['files']:
        entry['archive']['sourceURL'] = NEW_BASE + entry['archive']['path']
    (args.output / manifest_name).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
    paths = sorted(args.output.iterdir())
    (args.output / 'SHA256SUMS').write_text(''.join(f'{sha256(path)}  {path.name}\n' for path in paths))
    print(f'Staged and verified {len(assets)} repair payloads and {len(files)} model entries in {args.output}')


if __name__ == '__main__':
    main()
