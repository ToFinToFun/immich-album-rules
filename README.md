# Immich Auto Archive

Automatically apply visibility rules to assets in selected Immich albums so phone folders such as Screenshots, Downloads, Messenger or WhatsApp can be kept out of the main photo timeline.

Version **0.2.0** supports three per-album actions:

- **Archive** — keep assets in the album but hide them from the main timeline.
- **Locked** — move matching assets into Immich Locked Folder.
- **Timeline** — restore matching archived assets to the normal timeline.

The tool lives **outside Immich's application directories**, so normal Immich upgrades do not overwrite its program, configuration, API keys, or systemd timer.

## Important Locked Folder limitation

Immich treats Locked Folder differently from Archive. When an asset is set to `locked`, Immich itself removes that asset from **all albums**. Immich also requires an elevated interactive session to search Locked Folder; a normal API key cannot browse those assets.

Therefore a `Locked` rule is intentionally treated as a **one-way album -> Locked Folder action**:

- Timeline assets in the source album can be moved to Locked.
- Archived assets still in the source album can be moved to Locked.
- Once locked, Immich removes them from the source album.
- Changing/removing the rule later does **not** automatically restore assets already in Locked Folder.
- Existing locked assets must be unlocked/restored from inside Immich.

The interactive menu warns before a Locked rule is created or selected.

## Rule priority

If the same asset is present in multiple source albums with conflicting rules, the safer visibility wins:

```text
Locked > Archive > Timeline
```

The decision is made before any changes are sent to Immich.

## Default rules for newly discovered users

All built-in defaults use **Archive**:

1. Screenshots
2. Download
3. WhatsApp
4. WhatsApp Images
5. WhatsApp Video
6. Facebook
7. Messenger
8. Messages

Every user gets an independent copy of the defaults and can then edit, add, remove, or change actions without affecting other users.

## Upgrade from v0.1.x

Upgrade is automatic. Existing configuration and API keys are preserved. Every existing v0.1.x album entry is migrated to an **Archive** rule, so current behavior does not change unexpectedly.

## Install / upgrade

From an extracted source tree:

```bash
sudo ./install.sh
immich-auto-archive --doctor
immich-auto-archive
```

Re-running `install.sh` updates the program files while preserving `/etc/immich-auto-archive`.

## API key setup

Each Immich user needs their **own** API key. The same key supports Archive, Locked and Timeline rules.

For each user:

1. Sign in to the Immich web interface as that user.
2. Click the profile icon in the top-right corner.
3. Open **Account Settings -> API Keys**.
4. Click **New API Key**.
5. Name it `Immich Auto Archive`.
6. Grant only:
   - `user.read`
   - `album.read`
   - `asset.read`
   - `asset.update`
7. Create and copy the key.
8. Run `immich-auto-archive`, select the user, and choose **Add / replace API key (guided)**.

Keys are validated against the selected Immich user and stored in `/etc/immich-auto-archive/keys/<user-id>.key` with mode `0600`. They are never stored in `config.json`.

## Album Sync prerequisite

The tool works with **Immich server albums**, not Android/iOS folders directly. If photos are uploaded but the expected server albums do not exist, enable **Backup album synchronization / Album Sync** in the Immich mobile app and use **Reorganize into album** for already uploaded photos.

When zero server albums are found, the tool prints this guidance instead of repeating `not found` for every configured rule.

## Interactive management

```bash
immich-auto-archive
```

The menu supports:

- automatic Immich user discovery
- API-key status per user
- add/change/remove rules per user
- Archive / Locked / Timeline action selection
- reset user rules to defaults
- list detected Immich server albums and their configured action
- per-user sync and dry-run
- global sync and dry-run
- enable/disable a user's automatic rules
- logs, status and doctor checks

## Commands

```bash
immich-auto-archive              # interactive menu
immich-auto-archive --sync       # apply all rules now
immich-auto-archive --dry-run    # show planned visibility changes
immich-auto-archive --status     # configuration + timer status
immich-auto-archive --doctor     # installation/API checks
immich-auto-archive --refresh-users
```

## How synchronization works

1. Discovers users with `immich-admin list-users` (or a supported Docker container).
2. Uses each user's own API key.
3. Finds matching Immich server albums.
4. Reads only that user's assets from those albums.
5. Evaluates Timeline and Archive assets and builds one desired visibility plan.
6. Resolves conflicts with `Locked > Archive > Timeline`.
7. Applies bulk visibility changes through Immich's API.

Shared albums are protected: assets owned by somebody else are never changed.

## Files installed

```text
/opt/immich-auto-archive/                 application code
/etc/immich-auto-archive/config.json      configuration
/etc/immich-auto-archive/keys/            per-user API keys
/usr/local/bin/immich-auto-archive        command
/etc/systemd/system/immich-auto-archive.service
/etc/systemd/system/immich-auto-archive.timer
```

This deliberately avoids `/opt/immich`.

## Uninstall

```bash
sudo ./uninstall.sh          # keep config/API keys
sudo ./uninstall.sh --purge  # remove everything
```

## Safety

- No direct database writes.
- No media files are deleted.
- No media files are physically moved by this tool.
- Only Immich visibility state is changed through the official API.
- API keys are checked against the selected user before being accepted.
- `--dry-run` is available before real changes.
- Locked Folder behavior is controlled by Immich itself and is clearly warned about.

## License

MIT
