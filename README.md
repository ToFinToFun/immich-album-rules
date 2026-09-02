# Immich Album Rules

Automatically apply visibility rules to assets in selected Immich albums.

Immich Album Rules is useful for phone-synced folders such as Screenshots, Downloads, Messenger or WhatsApp that should not necessarily remain in the main Immich timeline.

Version **0.2.0** supports three per-album actions:

- **Archive** — keep matching assets in the album but hide them from the main timeline.
- **Locked** — move matching assets into Immich Locked Folder.
- **Timeline** — restore matching archived assets to the normal timeline.

The project was previously named **Immich Auto Archive**. Version 0.2.0 renames it to **Immich Album Rules** because it now manages more than Archive visibility.

## Upgrade from Immich Auto Archive

The v0.2.0 installer automatically detects an existing `immich-auto-archive` installation and migrates it:

- `/etc/immich-auto-archive` is moved to `/etc/immich-album-rules` when the new config directory does not already exist.
- Existing API keys and per-user rules are preserved.
- Old v0.1.x album entries are migrated to **Archive** rules.
- The old `immich-auto-archive.timer` is disabled and replaced by `immich-album-rules.timer`.
- The obsolete application directory and systemd units are removed.
- `/usr/local/bin/immich-auto-archive` remains as a compatibility alias and prints a rename notice before running `immich-album-rules`.

No API key needs to be recreated just because of the rename. An existing key named `Immich Auto Archive` in Immich can continue to be used.

## Important Locked Folder limitation

Immich treats Locked Folder differently from Archive. When an asset is set to `locked`, Immich itself removes that asset from **all albums**. Immich also requires an elevated interactive session to search Locked Folder; a normal API key cannot browse those assets.

Therefore a `Locked` rule is intentionally treated as a **one-way album -> Locked Folder action**:

- Timeline assets in the source album can be moved to Locked.
- Archived assets still in the source album can be moved to Locked.
- Once locked, Immich removes them from the source album.
- Changing or removing the rule later does **not** automatically restore assets already in Locked Folder.
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

## Install / upgrade

From an extracted source tree:

```bash
sudo ./install.sh
immich-album-rules --doctor
immich-album-rules
```

Re-running `install.sh` updates the program while preserving `/etc/immich-album-rules`.

## API key setup

Each Immich user needs their **own** API key. The same key supports Archive, Locked and Timeline rules.

For each user:

1. Sign in to the Immich web interface as that user.
2. Click the profile icon in the top-right corner.
3. Open **Account Settings -> API Keys**.
4. Click **New API Key**.
5. Name it `Immich Album Rules`.
6. Grant only:
   - `user.read`
   - `album.read`
   - `asset.read`
   - `asset.update`
7. Create and copy the key.
8. Run `immich-album-rules`, select the user, and choose **Add / replace API key (guided)**.

Keys are validated against the selected Immich user and stored in `/etc/immich-album-rules/keys/<user-id>.key` with mode `0600`. They are never stored in `config.json`.

## Album Sync prerequisite

The tool works with **Immich server albums**, not Android/iOS folders directly. If photos are uploaded but the expected server albums do not exist, enable **Backup album synchronization / Album Sync** in the Immich mobile app and use **Reorganize into album** for already uploaded photos.

When zero server albums are found, the tool prints this guidance instead of repeating `not found` for every configured rule.

## Interactive management

```bash
immich-album-rules
```

The menu supports automatic Immich user discovery, API-key status per user, add/change/remove rules, Archive/Locked/Timeline action selection, default resets, detected album listing, per-user/global sync and dry-run, enable/disable, logs, status and doctor checks.

## Commands

```bash
immich-album-rules              # interactive menu
immich-album-rules --sync       # apply all rules now
immich-album-rules --dry-run    # show planned visibility changes
immich-album-rules --status     # configuration + timer status
immich-album-rules --doctor     # installation/API checks
immich-album-rules --refresh-users
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
/opt/immich-album-rules/                 application code
/etc/immich-album-rules/config.json      configuration
/etc/immich-album-rules/keys/            per-user API keys
/usr/local/bin/immich-album-rules        command
/etc/systemd/system/immich-album-rules.service
/etc/systemd/system/immich-album-rules.timer
```

This deliberately avoids Immich's own application directories.

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
