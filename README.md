# Waifu2x Desktop Models

This public repository publishes versioned model weights and helper graphs for [Waifu2x Desktop](https://github.com/SteveTanSaMa/Waifu2x-Desktop). The app downloads models on demand; it keeps its ONNX Runtime and ncnn inference engine bundled separately. This repository does not contain the large model files in Git: GitHub Release assets carry the archives.

## Published assets

Each immutable model set has its own tag, such as `models-v1`, independently of application versions:

- `Waifu2x-models-v1.tar.xz`: the complete first-install archive.
- `Waifu2x-models-v1-repair.json` and `Waifu2x-model-<sha256>.tar.xz`: the repair index and one archive for each distinct model file content.
- `SHA256SUMS`: hashes for the release assets.

[catalog/models-v1.json](catalog/models-v1.json) records the original source, byte count and SHA-256 of each file, and the fixed release URLs and hash of the complete archive. The app embeds a compatible catalog and verifies the archive and every extracted file. Its repair index cannot change the expected model bytes. App versions can continue using the same model release; publishing a different model set does not automatically upgrade an installed app. Keep older releases and assets available, because installed app versions refer to fixed URLs.

## Preparing a release

Use Python 3, an empty output directory, and a verified local model cache. For a new version, prepare a new catalog in `catalog/`, assign a new `models-vN` version, and record exact paths, upstream URLs, sizes and hashes. Coordinate its compatibility and catalog with the Desktop app. Never alter an existing published model release or reuse its version for different bytes.

```sh
python3 script/package_models.py --catalog catalog/models-v1.json --root /path/to/verified/models-v1 --output-dir dist --update-catalog
python3 -m unittest discover -s tests -v
```

The packager verifies every input file against the catalog before building deterministic tar.xz archives and a content-addressed repair index. `--update-catalog` writes the resulting full-archive size, hash, fixed URL, and repair index URL into the selected catalog. Review and commit the catalog before publishing; app builds must pin that same catalog. Test the actual model set with the compatible Desktop app as well.

Create `SHA256SUMS` over the complete archive, repair index and every repair archive. Review the release contents before creating a draft at the committed catalog revision; upload the assets to the draft, verify their sizes and SHA-256 hashes after download, then publish it. For example, from `dist` on macOS:

```sh
shasum -a 256 Waifu2x-models-v1.tar.xz Waifu2x-models-v1-repair.json Waifu2x-model-*.tar.xz > SHA256SUMS
shasum -a 256 -c SHA256SUMS
```

Do not replace assets after publication. New model sets need a new version; older applications may otherwise fail verification or lose access to repairs. Model redistribution rights and notices must be reviewed when adding or replacing models.

## Initial v1 migration

The initial release reuses the exact verified v1.0.3 Desktop model assets. Download `SHA256SUMS`, `Waifu2x-models-v1.tar.xz`, `Waifu2x-models-v1-repair.json`, and all `Waifu2x-model-*.tar.xz` from that release to a local directory. Then run:

```sh
python3 script/import_legacy.py --source /path/to/downloaded/assets
```

The importer checks the legacy release checksum file, pinned complete-archive hash, every manifest entry, each compressed payload, and the decompressed model hash. It stages the unchanged archives, a rewritten fixed-URL index, and `SHA256SUMS` under an empty `dist/`. Keep the old release assets in place: older app versions still refer to them. Verify the published new release independently before changing the app catalog.

## Sources and licenses

ONNX models and helper graphs are redistributed unchanged from [nagadomi/nunif](https://github.com/nagadomi/nunif/tree/master/waifu2x/unlimited_waifu2x), published at [unlimited.waifu2x.net/models](https://unlimited.waifu2x.net/models/); see [nunif license](licenses/nunif.txt). CUNet and Upconv ncnn files are from [waifu2x-ncnn-vulkan](https://github.com/nihui/waifu2x-ncnn-vulkan/tree/a86cfb043e6482b5f08778a114df9b5faf6e73b7/models), fixed at commit `a86cfb0`; see [its license](licenses/LICENSE-waifu2x), [waifu2x](licenses/waifu2x-original.txt), and [waifu2x-caffe](licenses/waifu2x-caffe.txt). Per-file provenance is retained in the catalog. Image processing happens locally; the model download does not upload images.
