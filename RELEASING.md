# Releasing Immich Album Rules

GitHub Actions handles releases through `.github/workflows/release.yml`.

## Recommended: manual Release workflow

1. Update `VERSION` in `src/immich_album_rules.py` and commit it to `main`.
2. Open **Actions -> Release -> Run workflow** in GitHub.
3. Enter the same version, for example `0.2.1` (leading `v` is optional).
4. Run the workflow.

The workflow verifies the version, runs tests, builds and extracts the standalone installer, creates the Git tag, creates the GitHub Release, and uploads `immich-album-rules-<version>-installer.sh`.

GitHub automatically provides Source code ZIP/TAR downloads.

## ChatGPT-triggered release

The workflow also watches `.github/release-request`. Updating its `request=` value on `main` triggers a release for the `version=` in that file. This allows the connected GitHub integration to start the release even though it does not expose GitHub's Create Release API directly.

## Tag-driven release

A pushed tag matching `v*` also triggers the same workflow. The tag version must match `VERSION` in `src/immich_album_rules.py`.

## Pre-releases

Versions containing a suffix such as `0.3.0-beta.1` are automatically published as pre-releases.

## Build locally

```bash
bash scripts/build-release.sh
```

or:

```bash
bash scripts/build-release.sh 0.2.1
```

The installer is written to `dist/`.
