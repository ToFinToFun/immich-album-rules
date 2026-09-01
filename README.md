# Immich Auto Archive

Automatically archive assets from selected Immich albums so they remain available in albums and Archive, but stay out of the main timeline.

The tool is designed to live **outside Immich's application directories**, so normal Immich upgrades do not overwrite its program, configuration, API keys, or systemd timer.

## Current status

Early working release intended first for testing on an Immich Community Scripts / Proxmox LXC installation. It also supports local `immich-admin` installations and common Docker container names for user discovery.

## Default albums for newly discovered users

1. Screenshots
2. Download
3. WhatsApp
4. WhatsApp Images
5. WhatsApp Video
6. Facebook
7. Messenger
8. Messages

Each user gets an independent copy of the default list and can then be edited without affecting other users.

## Install

```bash
sudo ./install.sh
immich-auto-archive --doctor
immich-auto-archive
```

Re-running `install.sh` updates the program files while preserving `/etc/immich-auto-archive`.

## API keys

Each Immich user needs their own API key. Create it while logged in as that user and grant only:

- `user.read`
- `album.read`
- `asset.read`
- `asset.update`

The interactive menu stores keys in `/etc/immich-auto-archive/keys/<user-id>.key` with mode `0600`. Keys are never stored in `config.json`.

## Commands

```bash
immich-auto-archive              # interactive menu
immich-auto-archive --sync       # sync all users now
immich-auto-archive --dry-run    # show changes without archiving
immich-auto-archive --status     # configuration + timer status
immich-auto-archive --doctor     # installation/API checks
immich-auto-archive --refresh-users
```

## How it works

1. Discovers Immich users using `immich-admin list-users` (or `docker exec ... immich-admin list-users`).
2. Gives newly discovered users the configured default album list.
3. For each configured user with an API key, finds matching Immich albums.
4. Searches only `timeline` assets in each album.
5. Filters results to assets owned by that user, which avoids modifying another user's files in shared albums.
6. Bulk-updates those assets to `visibility=archive`.

If multiple Immich albums have the same name, all matching albums are processed. Missing album names are skipped rather than treated as errors.

## Files installed

```text
/opt/immich-auto-archive/                 application code
/etc/immich-auto-archive/config.json      configuration
/etc/immich-auto-archive/keys/            per-user API keys
/usr/local/bin/immich-auto-archive        command
/etc/systemd/system/immich-auto-archive.service
/etc/systemd/system/immich-auto-archive.timer
```

This deliberately avoids `/opt/immich`, which Community Scripts updates in place.

## Uninstall

```bash
sudo ./uninstall.sh          # keep config/API keys
sudo ./uninstall.sh --purge  # remove everything
```

## Safety

- No direct database writes.
- No files are moved or deleted.
- Only asset visibility changes from `timeline` to `archive`.
- `--dry-run` is provided for first-run validation.
- API keys are checked against the selected Immich user before being accepted.

## License

MIT
