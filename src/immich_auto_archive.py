#!/usr/bin/env python3
"""Immich Auto Archive - automatically archive assets from selected Immich albums.

Designed to be installed outside Immich's own application tree so normal Immich
upgrades do not overwrite it.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

VERSION = "0.1.1-dev"
DEFAULT_CONFIG_DIR = Path(os.environ.get("IMMICH_AUTO_ARCHIVE_CONFIG_DIR", "/etc/immich-auto-archive"))
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"
DEFAULT_KEYS_DIR = DEFAULT_CONFIG_DIR / "keys"
DEFAULT_SERVER_URL = "http://127.0.0.1:2283/api"
DEFAULT_ALBUMS = [
    "Screenshots",
    "Download",
    "WhatsApp",
    "WhatsApp Images",
    "WhatsApp Video",
    "Facebook",
    "Messenger",
    "Messages",
]
REQUIRED_KEY_PERMISSIONS = ["user.read", "album.read", "asset.read", "asset.update"]


class AppError(RuntimeError):
    pass


@dataclass
class DiscoveredUser:
    id: str
    email: str
    name: str
    is_admin: bool = False

    @property
    def label(self) -> str:
        if self.name and self.email:
            return f"{self.name} <{self.email}>"
        return self.name or self.email or self.id


def default_config() -> dict[str, Any]:
    return {
        "version": 1,
        "server_url": DEFAULT_SERVER_URL,
        "sync_interval_minutes": 5,
        "default_albums": list(DEFAULT_ALBUMS),
        "users": {},
    }


def ensure_config(config_file: Path = DEFAULT_CONFIG_FILE) -> dict[str, Any]:
    config_file.parent.mkdir(parents=True, exist_ok=True)
    keys_dir = config_file.parent / "keys"
    keys_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(config_file.parent, 0o700)
        os.chmod(keys_dir, 0o700)
    except PermissionError:
        pass

    if not config_file.exists():
        cfg = default_config()
        save_config(cfg, config_file)
        return cfg

    with config_file.open("r", encoding="utf-8") as fh:
        cfg = json.load(fh)

    # Forward-compatible defaults.
    base = default_config()
    for key, value in base.items():
        cfg.setdefault(key, value)
    cfg.setdefault("users", {})
    return cfg


def save_config(cfg: dict[str, Any], config_file: Path = DEFAULT_CONFIG_FILE) -> None:
    config_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_file.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, config_file)


def key_path(user_id: str, config_file: Path = DEFAULT_CONFIG_FILE) -> Path:
    return config_file.parent / "keys" / f"{user_id}.key"


def read_key(user_id: str, config_file: Path = DEFAULT_CONFIG_FILE) -> str | None:
    path = key_path(user_id, config_file)
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def write_key(user_id: str, api_key: str, config_file: Path = DEFAULT_CONFIG_FILE) -> None:
    path = key_path(user_id, config_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(api_key.strip() + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def remove_key(user_id: str, config_file: Path = DEFAULT_CONFIG_FILE) -> None:
    try:
        key_path(user_id, config_file).unlink()
    except FileNotFoundError:
        pass


def _run(cmd: list[str], timeout: int = 30) -> str:
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise AppError(f"Command failed ({' '.join(cmd)}): {detail}")
    return proc.stdout


def _parse_immich_admin_users(output: str) -> list[DiscoveredUser]:
    """Parse current immich-admin list-users human-readable JS-ish output."""
    users: list[DiscoveredUser] = []
    # Each object contains id/email/name; matching non-greedily avoids needing a JS parser.
    for block in re.findall(r"\{(.*?)\}", output, flags=re.S):
        def field(name: str) -> str:
            m = re.search(rf"\b{name}\s*:\s*'([^']*)'", block)
            return m.group(1) if m else ""

        user_id = field("id")
        if not user_id:
            continue
        deleted_match = re.search(r"\bdeletedAt\s*:\s*([^,\n]+)", block)
        if deleted_match and deleted_match.group(1).strip() not in {"null", "undefined"}:
            continue
        admin_match = re.search(r"\bisAdmin\s*:\s*(true|false)", block)
        users.append(
            DiscoveredUser(
                id=user_id,
                email=field("email"),
                name=field("name"),
                is_admin=bool(admin_match and admin_match.group(1) == "true"),
            )
        )
    return users


def discover_users() -> tuple[list[DiscoveredUser], str]:
    """Discover Immich users without an API key.

    Prefers a local immich-admin binary (Community Scripts/LXC, native installs),
    then tries common Docker container names.
    """
    if shutil.which("immich-admin"):
        output = _run(["immich-admin", "list-users"], timeout=60)
        users = _parse_immich_admin_users(output)
        if users or "[]" in output:
            return users, "local immich-admin"

    if shutil.which("docker"):
        for container in ("immich_server", "immich-server"):
            proc = subprocess.run(
                ["docker", "exec", container, "immich-admin", "list-users"],
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
            if proc.returncode == 0:
                users = _parse_immich_admin_users(proc.stdout)
                if users or "[]" in proc.stdout:
                    return users, f"docker:{container}"

    raise AppError(
        "Could not discover Immich users. Expected a local 'immich-admin' command "
        "or a Docker container named immich_server/immich-server."
    )


def refresh_users(cfg: dict[str, Any], config_file: Path = DEFAULT_CONFIG_FILE) -> tuple[list[DiscoveredUser], str, bool]:
    users, source = discover_users()
    changed = False
    configured = cfg.setdefault("users", {})
    defaults = cfg.get("default_albums") or list(DEFAULT_ALBUMS)

    for user in users:
        entry = configured.get(user.id)
        if entry is None:
            configured[user.id] = {
                "name": user.name,
                "email": user.email,
                "enabled": True,
                "albums": list(defaults),
            }
            changed = True
        else:
            if entry.get("name") != user.name:
                entry["name"] = user.name
                changed = True
            if entry.get("email") != user.email:
                entry["email"] = user.email
                changed = True
            entry.setdefault("enabled", True)
            entry.setdefault("albums", list(defaults))

    if changed:
        save_config(cfg, config_file)
    return users, source, changed


class ImmichApi:
    def __init__(self, base_url: str, api_key: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = None
        headers = {"Accept": "application/json", "x-api-key": self.api_key}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                if not raw:
                    return None
                ctype = resp.headers.get("Content-Type", "")
                if "json" in ctype or raw[:1] in (b"{", b"["):
                    return json.loads(raw.decode("utf-8"))
                return raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AppError(f"Immich API {method} {path} returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise AppError(f"Cannot reach Immich API at {self.base_url}: {exc.reason}") from exc

    def current_user(self) -> dict[str, Any]:
        return self.request("GET", "/users/me")

    def albums(self) -> list[dict[str, Any]]:
        data = self.request("GET", "/albums")
        if not isinstance(data, list):
            raise AppError("Unexpected response from GET /albums")
        return data

    def timeline_asset_ids_for_album(self, album_id: str, owner_id: str) -> list[str]:
        ids: list[str] = []
        page = 1
        while True:
            result = self.request(
                "POST",
                "/search/metadata",
                {
                    "albumIds": [album_id],
                    "visibility": "timeline",
                    "page": page,
                    "size": 1000,
                },
            )
            assets_block = (result or {}).get("assets", {})
            items = assets_block.get("items", []) or []
            for asset in items:
                # Shared albums may contain another user's assets. Never change those.
                if asset.get("ownerId") == owner_id:
                    asset_id = asset.get("id")
                    if asset_id:
                        ids.append(asset_id)
            next_page = assets_block.get("nextPage")
            if not next_page:
                break
            try:
                page = int(next_page)
            except (TypeError, ValueError):
                raise AppError(f"Unexpected pagination token from Immich: {next_page!r}")
        return ids

    def archive_ids(self, ids: list[str], chunk_size: int = 500) -> int:
        count = 0
        for start in range(0, len(ids), chunk_size):
            chunk = ids[start : start + chunk_size]
            payload = {"ids": chunk, "visibility": "archive"}
            # v3 uses PATCH. PUT remains as a compatibility fallback for v2-era servers.
            try:
                self.request("PATCH", "/assets", payload)
            except AppError as exc:
                if "HTTP 404" not in str(exc) and "HTTP 405" not in str(exc):
                    raise
                self.request("PUT", "/assets", payload)
            count += len(chunk)
        return count


@dataclass
class SyncResult:
    user_id: str
    label: str
    archived: int = 0
    would_archive: int = 0
    missing_albums: list[str] | None = None
    no_server_albums: bool = False
    skipped: str | None = None
    error: str | None = None


def validate_api_key(cfg: dict[str, Any], user: DiscoveredUser, config_file: Path = DEFAULT_CONFIG_FILE) -> tuple[bool, str]:
    api_key = read_key(user.id, config_file)
    if not api_key:
        return False, "missing"
    try:
        me = ImmichApi(cfg["server_url"], api_key).current_user()
    except AppError as exc:
        return False, str(exc)
    if me.get("id") != user.id:
        return False, f"belongs to {me.get('name') or me.get('email') or me.get('id', 'another user')}"
    return True, "valid"


def sync_user(
    cfg: dict[str, Any],
    user: DiscoveredUser,
    *,
    dry_run: bool = False,
    config_file: Path = DEFAULT_CONFIG_FILE,
    verbose: bool = True,
) -> SyncResult:
    entry = cfg.get("users", {}).get(user.id, {})
    result = SyncResult(user_id=user.id, label=user.label, missing_albums=[])
    if not entry.get("enabled", True):
        result.skipped = "disabled"
        return result

    api_key = read_key(user.id, config_file)
    if not api_key:
        result.skipped = "API key missing"
        return result

    api = ImmichApi(cfg["server_url"], api_key)
    try:
        me = api.current_user()
        if me.get("id") != user.id:
            raise AppError("Stored API key belongs to a different Immich user")

        albums = api.albums()
        if not albums:
            result.no_server_albums = True
            if verbose:
                print("  No Immich server albums found for this user.")
                print("  Photos may be backed up without their phone folders being synced as albums.")
                print("  In the Immich mobile app:")
                print("    1. Enable Backup album synchronization / Album Sync")
                print("    2. Run Reorganize into album for already-uploaded photos")
            return result

        by_name: dict[str, list[dict[str, Any]]] = {}
        for album in albums:
            by_name.setdefault(album.get("albumName", ""), []).append(album)

        seen: set[str] = set()
        for album_name in entry.get("albums", []):
            matches = by_name.get(album_name, [])
            if not matches:
                result.missing_albums.append(album_name)
                if verbose:
                    print(f"  {album_name}: not found (skipped)")
                continue

            album_ids: list[str] = []
            for album in matches:
                album_ids.extend(api.timeline_asset_ids_for_album(album["id"], user.id))
            album_ids = [asset_id for asset_id in album_ids if not (asset_id in seen or seen.add(asset_id))]

            if dry_run:
                result.would_archive += len(album_ids)
                if verbose:
                    suffix = "asset" if len(album_ids) == 1 else "assets"
                    print(f"  {album_name}: {len(album_ids)} {suffix} would be archived")
            else:
                archived = api.archive_ids(album_ids) if album_ids else 0
                result.archived += archived
                if verbose:
                    suffix = "asset" if archived == 1 else "assets"
                    print(f"  {album_name}: {archived} {suffix} archived")

    except AppError as exc:
        result.error = str(exc)
    return result


def sync_all(cfg: dict[str, Any], *, dry_run: bool, config_file: Path, verbose: bool = True) -> list[SyncResult]:
    users, _, _ = refresh_users(cfg, config_file)
    results: list[SyncResult] = []
    for user in users:
        if verbose:
            print(f"\n{user.label}")
        result = sync_user(cfg, user, dry_run=dry_run, config_file=config_file, verbose=verbose)
        results.append(result)
        if verbose and result.skipped:
            print(f"  SKIP: {result.skipped}")
        if verbose and result.error:
            print(f"  ERROR: {result.error}")
    return results


def print_summary(results: Iterable[SyncResult], dry_run: bool) -> int:
    results = list(results)
    total = sum(r.would_archive if dry_run else r.archived for r in results)
    errors = [r for r in results if r.error]
    action = "would be archived" if dry_run else "archived"
    print(f"\nTotal: {total} assets {action}.")
    if errors:
        print(f"Errors: {len(errors)} user(s).")
        return 1
    return 0


def format_key_status(cfg: dict[str, Any], user: DiscoveredUser, config_file: Path) -> str:
    if not read_key(user.id, config_file):
        return "NO KEY"
    ok, detail = validate_api_key(cfg, user, config_file)
    return "OK" if ok else f"INVALID ({detail})"


def pause() -> None:
    input("\nPress Enter to continue...")


def clear() -> None:
    if sys.stdout.isatty():
        os.system("clear")


def print_api_key_guide(user: DiscoveredUser) -> None:
    print("\nCREATE IMMICH API KEY")
    print("=" * 60)
    print("API keys are per Immich user. Create the key while signed in as:")
    print(f"  {user.label}")
    print("\n1. Open the Immich web interface and sign in as that user.")
    print("2. Click the profile icon in the top-right corner.")
    print("3. Open: Account Settings -> API Keys")
    print("4. Click: New API Key")
    print("5. Name it: Immich Auto Archive")
    print("6. Grant ONLY these permissions:")
    for perm in REQUIRED_KEY_PERMISSIONS:
        print(f"     - {perm}")
    print("7. Create the key and copy it.")
    print("\nThe key will be stored locally with file mode 0600 and is not")
    print("written to config.json. The key is validated before it is saved.")


def show_detected_albums(
    cfg: dict[str, Any],
    user: DiscoveredUser,
    config_file: Path = DEFAULT_CONFIG_FILE,
) -> None:
    """Show all Immich server albums visible to the selected user."""
    api_key = read_key(user.id, config_file)
    if not api_key:
        print("No API key is configured for this user.")
        print("Add an API key first so Immich Auto Archive can read their server albums.")
        return

    try:
        api = ImmichApi(cfg["server_url"], api_key)
        me = api.current_user()
        if me.get("id") != user.id:
            raise AppError("Stored API key belongs to a different Immich user")
        albums = api.albums()
    except AppError as exc:
        print(f"ERROR: {exc}")
        return

    print(f"DETECTED IMMICH ALBUMS - {user.label}")
    print("=" * 72)
    if not albums:
        print("No Immich server albums found for this user.")
        print()
        print("If photos from phone folders are already backed up, enable")
        print("Backup album synchronization / Album Sync in the Immich mobile app")
        print("and run Reorganize into album for already-uploaded photos.")
        return

    configured = set(cfg.get("users", {}).get(user.id, {}).get("albums", []))
    albums = sorted(albums, key=lambda a: (str(a.get("albumName", "")).casefold(), str(a.get("id", ""))))
    for i, album in enumerate(albums, 1):
        name = album.get("albumName") or "(unnamed)"
        count = album.get("assetCount")
        count_text = "? assets" if count is None else f"{count} {'asset' if count == 1 else 'assets'}"
        marker = "AUTO" if name in configured else "----"
        print(f"{i:3}. [{marker}] {name} ({count_text})")

    print(f"\nDetected: {len(albums)} server album{'s' if len(albums) != 1 else ''}")
    print("[AUTO] = album name is configured for automatic archiving")

def manage_user(cfg: dict[str, Any], user: DiscoveredUser, config_file: Path) -> None:
    while True:
        entry = cfg["users"][user.id]
        clear()
        print("IMMICH AUTO ARCHIVE")
        print("=" * 60)
        print(user.label)
        print(f"API key:       {format_key_status(cfg, user, config_file)}")
        print(f"Auto archive:  {'ENABLED' if entry.get('enabled', True) else 'DISABLED'}")
        print("\nAlbums:")
        for i, album in enumerate(entry.get("albums", []), 1):
            print(f"  {i:2}. {album}")
        if not entry.get("albums"):
            print("  (none)")
        print("\n1) Add / replace API key (guided)")
        print("2) Remove API key")
        print("3) Add album")
        print("4) Remove album")
        print("5) Reset albums to defaults")
        print("6) Sync this user now")
        print("7) Dry-run this user")
        print("8) Enable / disable auto archive")
        print("9) Test API key")
        print("10) Show detected Immich albums")
        print("0) Back")
        choice = input("\nSelect: ").strip().lower()

        if choice == "0":
            return
        if choice == "1":
            print_api_key_guide(user)
            ready = input("\nPress Enter when the key is copied, or Q to cancel: ").strip().lower()
            if ready == "q":
                continue
            api_key = getpass.getpass("Paste API key (input hidden): ").strip()
            if not api_key:
                print("No key entered.")
                pause()
                continue
            # Validate before storing when possible.
            try:
                me = ImmichApi(cfg["server_url"], api_key).current_user()
                if me.get("id") != user.id:
                    print("ERROR: This API key belongs to a different Immich user.")
                    pause()
                    continue
            except AppError as exc:
                print(f"ERROR: {exc}")
                pause()
                continue
            write_key(user.id, api_key, config_file)
            print("API key stored securely (0600).")
            pause()
        elif choice == "2":
            if input("Remove the locally stored API key? [y/N]: ").strip().lower() == "y":
                remove_key(user.id, config_file)
                print("API key removed from Immich Auto Archive.")
            pause()
        elif choice == "3":
            name = input("Album name to add: ").strip()
            if name and name not in entry["albums"]:
                entry["albums"].append(name)
                save_config(cfg, config_file)
                print(f"Added: {name}")
            elif name:
                print("That album is already configured.")
            pause()
        elif choice == "4":
            albums = entry.get("albums", [])
            if not albums:
                print("No albums configured.")
                pause()
                continue
            value = input("Album number or exact name to remove: ").strip()
            removed = None
            if value.isdigit() and 1 <= int(value) <= len(albums):
                removed = albums.pop(int(value) - 1)
            elif value in albums:
                albums.remove(value)
                removed = value
            if removed:
                save_config(cfg, config_file)
                print(f"Removed: {removed}")
            else:
                print("Album not found in configuration.")
            pause()
        elif choice == "5":
            entry["albums"] = list(cfg.get("default_albums", DEFAULT_ALBUMS))
            save_config(cfg, config_file)
            print("Album list reset to current defaults.")
            pause()
        elif choice in {"6", "7"}:
            dry = choice == "7"
            print(f"\n{'Dry-run' if dry else 'Sync'} for {user.label}")
            result = sync_user(cfg, user, dry_run=dry, config_file=config_file, verbose=True)
            if result.error:
                print(f"ERROR: {result.error}")
            else:
                value = result.would_archive if dry else result.archived
                print(f"\nTotal: {value}")
            pause()
        elif choice == "8":
            entry["enabled"] = not entry.get("enabled", True)
            save_config(cfg, config_file)
        elif choice == "9":
            ok, detail = validate_api_key(cfg, user, config_file)
            print(f"API key: {'OK' if ok else 'FAILED'} - {detail}")
            pause()
        elif choice == "10":
            clear()
            show_detected_albums(cfg, user, config_file)
            pause()


def edit_defaults(cfg: dict[str, Any], config_file: Path) -> None:
    while True:
        clear()
        albums = cfg.get("default_albums", [])
        print("DEFAULT ALBUMS FOR NEW USERS")
        print("=" * 60)
        for i, album in enumerate(albums, 1):
            print(f"  {i:2}. {album}")
        print("\n1) Add default album")
        print("2) Remove default album")
        print("3) Restore built-in defaults")
        print("0) Back")
        choice = input("\nSelect: ").strip()
        if choice == "0":
            return
        if choice == "1":
            name = input("Album name: ").strip()
            if name and name not in albums:
                albums.append(name)
                save_config(cfg, config_file)
        elif choice == "2":
            value = input("Album number or exact name: ").strip()
            if value.isdigit() and 1 <= int(value) <= len(albums):
                albums.pop(int(value) - 1)
                save_config(cfg, config_file)
            elif value in albums:
                albums.remove(value)
                save_config(cfg, config_file)
        elif choice == "3":
            cfg["default_albums"] = list(DEFAULT_ALBUMS)
            save_config(cfg, config_file)


def show_status(cfg: dict[str, Any], users: list[DiscoveredUser], config_file: Path) -> None:
    print("IMMICH AUTO ARCHIVE STATUS")
    print("=" * 70)
    print(f"Server: {cfg['server_url']}")
    print(f"Configured interval: {cfg.get('sync_interval_minutes', 5)} minutes")
    print()
    for user in users:
        entry = cfg["users"].get(user.id, {})
        status = format_key_status(cfg, user, config_file)
        enabled = "enabled" if entry.get("enabled", True) else "disabled"
        print(f"- {user.label}: {status}, {enabled}, {len(entry.get('albums', []))} albums")
    if shutil.which("systemctl"):
        print("\nTimer:")
        subprocess.run(["systemctl", "status", "immich-auto-archive.timer", "--no-pager"], check=False)


def doctor(cfg: dict[str, Any], config_file: Path) -> int:
    failures = 0
    print("Immich Auto Archive doctor")
    print("=" * 60)
    print(f"Version: {VERSION}")
    print(f"Config:  {config_file}")
    print(f"Server:  {cfg['server_url']}")
    print(f"Python:  {sys.version.split()[0]}")

    try:
        users, source, _ = refresh_users(cfg, config_file)
        print(f"Users:   OK ({len(users)} discovered via {source})")
    except AppError as exc:
        print(f"Users:   FAIL ({exc})")
        users = []
        failures += 1

    # Server connectivity can be tested with a user key if one exists.
    tested = False
    for user in users:
        if read_key(user.id, config_file):
            tested = True
            ok, detail = validate_api_key(cfg, user, config_file)
            print(f"API [{user.label}]: {'OK' if ok else 'FAIL'} ({detail})")
            if not ok:
                failures += 1
    if not tested:
        print("API:     SKIP (no user API keys configured yet)")

    if shutil.which("systemctl"):
        proc = subprocess.run(["systemctl", "is-enabled", "immich-auto-archive.timer"], text=True, capture_output=True)
        enabled = proc.stdout.strip()
        print(f"Timer:   {enabled or 'not installed'}")
    else:
        print("Timer:   SKIP (systemctl not available)")
    return 1 if failures else 0


def logs() -> None:
    if not shutil.which("journalctl"):
        print("journalctl is not available on this system.")
        return
    subprocess.run(["journalctl", "-u", "immich-auto-archive.service", "-n", "100", "--no-pager"], check=False)


def menu(cfg: dict[str, Any], config_file: Path) -> int:
    while True:
        try:
            users, source, _ = refresh_users(cfg, config_file)
        except AppError as exc:
            print(f"ERROR: {exc}")
            return 1
        clear()
        print(f"IMMICH AUTO ARCHIVE v{VERSION}")
        print("=" * 72)
        print(f"Immich: {cfg['server_url']}   User discovery: {source}")
        print()
        for i, user in enumerate(users, 1):
            entry = cfg["users"][user.id]
            if read_key(user.id, config_file):
                key_ok, _ = validate_api_key(cfg, user, config_file)
                key_state = "OK" if key_ok else "BAD KEY"
            else:
                key_state = "NO KEY"
            enabled = "ON" if entry.get("enabled", True) else "OFF"
            print(f"{i:2}) {user.label:<42.42} [{key_state:7}] [{enabled}] {len(entry.get('albums', []))} albums")
        print("\nA) Sync all now")
        print("D) Dry-run all")
        print("F) Edit default albums")
        print("R) Refresh users")
        print("S) Status")
        print("L) Logs")
        print("T) Doctor / test installation")
        print("0) Exit")
        choice = input("\nSelect user or action: ").strip().lower()
        if choice == "0":
            return 0
        if choice.isdigit() and 1 <= int(choice) <= len(users):
            manage_user(cfg, users[int(choice) - 1], config_file)
        elif choice == "a":
            results = sync_all(cfg, dry_run=False, config_file=config_file, verbose=True)
            print_summary(results, False)
            pause()
        elif choice == "d":
            results = sync_all(cfg, dry_run=True, config_file=config_file, verbose=True)
            print_summary(results, True)
            pause()
        elif choice == "f":
            edit_defaults(cfg, config_file)
        elif choice == "r":
            refresh_users(cfg, config_file)
        elif choice == "s":
            clear(); show_status(cfg, users, config_file); pause()
        elif choice == "l":
            clear(); logs(); pause()
        elif choice == "t":
            clear(); doctor(cfg, config_file); pause()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Automatically archive assets from selected Immich albums.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_FILE, help="Path to config.json")
    parser.add_argument("--sync", action="store_true", help="Synchronize all configured users now")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be archived without changing anything")
    parser.add_argument("--status", action="store_true", help="Show configuration and timer status")
    parser.add_argument("--doctor", action="store_true", help="Test installation, user discovery and configured API keys")
    parser.add_argument("--refresh-users", action="store_true", help="Refresh discovered Immich users")
    parser.add_argument("--quiet", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    args = parser.parse_args(argv)

    cfg = ensure_config(args.config)
    if args.refresh_users:
        users, source, changed = refresh_users(cfg, args.config)
        print(f"Discovered {len(users)} users via {source}. {'Configuration updated.' if changed else 'No changes.'}")
        return 0
    if args.doctor:
        return doctor(cfg, args.config)
    if args.status:
        users, _, _ = refresh_users(cfg, args.config)
        show_status(cfg, users, args.config)
        return 0
    if args.sync or args.dry_run:
        results = sync_all(cfg, dry_run=args.dry_run, config_file=args.config, verbose=not args.quiet)
        if args.quiet:
            errors = [r for r in results if r.error]
            for result in errors:
                print(f"ERROR [{result.label}]: {result.error}")
            total = sum(r.would_archive if args.dry_run else r.archived for r in results)
            if total:
                print(f"Immich Auto Archive: {total} assets {'would be archived' if args.dry_run else 'archived'}.")
            return 1 if errors else 0
        return print_summary(results, args.dry_run)
    return menu(cfg, args.config)


if __name__ == "__main__":
    raise SystemExit(main())
