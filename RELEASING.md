# Releasing Immich Auto Archive

GitHub Actions handles releases through `.github/workflows/release.yml`.

## Recommended: manual Release workflow

1. Update `VERSION` in `src/immich_auto_archive.py` to the version you want to publish and commit it to `main`.
2. Open **Actions -> Release -> Run workflow** in GitHub.
3. Enter the same version, for example `0.2.1` (the leading `v` is optional).
4. Run the workflow.

The workflow will:

- verify that the requested version matches the source code
- run the complete Python test suite
- build a standalone self-extracting installer
- extract and verify the generated installer payload
- create the Git tag if it does not already exist
- create the GitHub Release
- upload `immich-auto-archive-<version>-installer.sh` as the release asset

GitHub automatically provides its normal **Source code (zip)** and **Source code (tar.gz)** downloads, so they are not uploaded separately.

## Tag-driven release

A release is also triggered automatically when a tag matching `v*` is pushed, for example:

```bash
git tag -a v0.2.1 -m "Release v0.2.1"
git push origin v0.2.1
```

The tag version must match `VERSION` in `src/immich_auto_archive.py` or the workflow will stop without publishing a release.

## Pre-releases

Versions containing a suffix such as `0.3.0-beta.1` are automatically published as GitHub pre-releases.

## Build locally

The same standalone installer can be built without GitHub Actions:

```bash
bash scripts/build-release.sh
```

or with an explicit version check:

```bash
bash scripts/build-release.sh 0.2.1
```

The installer is written to `dist/`.

To verify/extract an installer without installing it:

```bash
bash dist/immich-auto-archive-0.2.1-installer.sh --extract /tmp/immich-auto-archive-test
```
