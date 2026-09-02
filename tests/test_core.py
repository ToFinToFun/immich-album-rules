import importlib.util
import json
import io
import contextlib
import tempfile
import threading
import sys
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("iaa", Path(__file__).parents[1] / "src" / "immich_auto_archive.py")
iaa = importlib.util.module_from_spec(SPEC)
sys.modules["iaa"] = iaa
SPEC.loader.exec_module(iaa)


class FakeImmich(BaseHTTPRequestHandler):
    key_user = "11111111-1111-4111-8111-111111111111"
    album_a = "22222222-2222-4222-8222-222222222222"
    album_b = "55555555-5555-4555-8555-555555555555"
    asset_a = "33333333-3333-4333-8333-333333333333"
    other_asset = "44444444-4444-4444-8444-444444444444"
    albums_response = None
    assets = {}
    updates = []
    searched_visibilities = []

    def log_message(self, *args):
        pass

    def _send(self, code, payload=None):
        raw = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.headers.get("x-api-key") != "good-key":
            return self._send(401, {"message": "bad key"})
        if self.path == "/api/users/me":
            return self._send(200, {"id": self.key_user, "name": "Jerry", "email": "j@example.test"})
        if self.path == "/api/albums":
            if self.__class__.albums_response is not None:
                return self._send(200, self.__class__.albums_response)
            return self._send(200, [{"id": self.album_a, "albumName": "Screenshots", "assetCount": 1}])
        return self._send(404, {})

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(size) or b"{}")
        if self.path == "/api/search/metadata":
            album_id = body["albumIds"][0]
            visibility = body["visibility"]
            self.__class__.searched_visibilities.append(visibility)
            items = []
            for asset_id, owner_id in self.__class__.assets.get((album_id, visibility), []):
                items.append({"id": asset_id, "ownerId": owner_id, "visibility": visibility})
            return self._send(200, {"assets": {"items": items, "nextPage": None}})
        return self._send(404, {})

    def do_PATCH(self):
        size = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(size) or b"{}")
        if self.path == "/api/assets":
            self.__class__.updates.append((tuple(body["ids"]), body["visibility"]))
            return self._send(204)
        return self._send(404, {})


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = HTTPServer(("127.0.0.1", 0), FakeImmich)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}/api"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        FakeImmich.albums_response = None
        FakeImmich.assets = {}
        FakeImmich.updates = []
        FakeImmich.searched_visibilities = []

    def _user_cfg(self, td, rules=None):
        cfgfile = Path(td) / "config.json"
        cfg = iaa.ensure_config(cfgfile)
        cfg["server_url"] = self.base
        cfg["users"] = {
            FakeImmich.key_user: {
                "name": "Jerry",
                "email": "j@example.test",
                "enabled": True,
                "rules": rules if rules is not None else [dict(r) for r in iaa.DEFAULT_RULES],
            }
        }
        iaa.save_config(cfg, cfgfile)
        iaa.write_key(FakeImmich.key_user, "good-key", cfgfile)
        user = iaa.DiscoveredUser(FakeImmich.key_user, "j@example.test", "Jerry")
        return cfgfile, cfg, user

    def test_default_rule_order_and_actions(self):
        self.assertEqual([r["album"] for r in iaa.DEFAULT_RULES], [
            "Screenshots", "Download", "WhatsApp", "WhatsApp Images",
            "WhatsApp Video", "Facebook", "Messenger", "Messages"
        ])
        self.assertTrue(all(r["action"] == "archive" for r in iaa.DEFAULT_RULES))

    def test_parse_users(self):
        text = """Initializing Immich v3\n[\n { id: '11111111-1111-4111-8111-111111111111', email: 'a@b', name: 'A', isAdmin: true, deletedAt: null, },\n]"""
        users = iaa._parse_immich_admin_users(text)
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0].name, "A")
        self.assertTrue(users[0].is_admin)

    def test_v01_config_migrates_to_archive_rules(self):
        with tempfile.TemporaryDirectory() as td:
            cfgfile = Path(td) / "config.json"
            cfgfile.write_text(json.dumps({
                "version": 1,
                "server_url": self.base,
                "default_albums": ["Screenshots", "Download"],
                "users": {
                    FakeImmich.key_user: {
                        "name": "Jerry", "email": "j@example.test", "enabled": True,
                        "albums": ["Screenshots", "Messenger"]
                    }
                }
            }))
            cfg = iaa.ensure_config(cfgfile)
            self.assertEqual(cfg["version"], 2)
            self.assertEqual(cfg["default_rules"], [
                {"album": "Screenshots", "action": "archive"},
                {"album": "Download", "action": "archive"},
            ])
            self.assertEqual(cfg["users"][FakeImmich.key_user]["rules"], [
                {"album": "Screenshots", "action": "archive"},
                {"album": "Messenger", "action": "archive"},
            ])
            self.assertNotIn("albums", cfg["users"][FakeImmich.key_user])
            self.assertNotIn("default_albums", cfg)

    def test_api_shared_asset_protection_and_generic_visibility(self):
        FakeImmich.assets[(FakeImmich.album_a, "timeline")] = [
            (FakeImmich.asset_a, FakeImmich.key_user),
            (FakeImmich.other_asset, "someone-else"),
        ]
        api = iaa.ImmichApi(self.base, "good-key")
        ids = api.asset_ids_for_album_visibility(FakeImmich.album_a, FakeImmich.key_user, "timeline")
        self.assertEqual(ids, [FakeImmich.asset_a])
        count = api.set_visibility(ids, "locked")
        self.assertEqual(count, 1)
        self.assertEqual(FakeImmich.updates, [((FakeImmich.asset_a,), "locked")])

    def test_locked_rule_moves_timeline_and_archive_assets_to_locked_without_querying_locked(self):
        FakeImmich.assets[(FakeImmich.album_a, "timeline")] = [(FakeImmich.asset_a, FakeImmich.key_user)]
        asset_b = "66666666-6666-4666-8666-666666666666"
        FakeImmich.assets[(FakeImmich.album_a, "archive")] = [(asset_b, FakeImmich.key_user)]
        with tempfile.TemporaryDirectory() as td:
            cfgfile, cfg, user = self._user_cfg(td, [{"album": "Screenshots", "action": "locked"}])
            result = iaa.sync_user(cfg, user, config_file=cfgfile, verbose=False)
            self.assertEqual(result.changed, 2)
            self.assertEqual(result.by_action["locked"], 2)
            self.assertEqual(FakeImmich.updates, [((FakeImmich.asset_a, asset_b), "locked")])
            self.assertNotIn("locked", FakeImmich.searched_visibilities)

    def test_archive_to_timeline_rule_restores_archived_asset(self):
        FakeImmich.assets[(FakeImmich.album_a, "archive")] = [(FakeImmich.asset_a, FakeImmich.key_user)]
        with tempfile.TemporaryDirectory() as td:
            cfgfile, cfg, user = self._user_cfg(td, [{"album": "Screenshots", "action": "timeline"}])
            result = iaa.sync_user(cfg, user, config_file=cfgfile, verbose=False)
            self.assertEqual(result.changed, 1)
            self.assertEqual(FakeImmich.updates, [((FakeImmich.asset_a,), "timeline")])

    def test_archive_to_locked_switch_handles_already_archived_asset(self):
        FakeImmich.assets[(FakeImmich.album_a, "archive")] = [(FakeImmich.asset_a, FakeImmich.key_user)]
        with tempfile.TemporaryDirectory() as td:
            cfgfile, cfg, user = self._user_cfg(td, [{"album": "Screenshots", "action": "locked"}])
            result = iaa.sync_user(cfg, user, config_file=cfgfile, verbose=False)
            self.assertEqual(result.changed, 1)
            self.assertEqual(FakeImmich.updates, [((FakeImmich.asset_a,), "locked")])

    def test_conflict_priority_locked_over_archive_over_timeline(self):
        FakeImmich.albums_response = [
            {"id": FakeImmich.album_a, "albumName": "A", "assetCount": 1},
            {"id": FakeImmich.album_b, "albumName": "B", "assetCount": 1},
        ]
        FakeImmich.assets[(FakeImmich.album_a, "timeline")] = [(FakeImmich.asset_a, FakeImmich.key_user)]
        FakeImmich.assets[(FakeImmich.album_b, "timeline")] = [(FakeImmich.asset_a, FakeImmich.key_user)]
        rules = [
            {"album": "A", "action": "archive"},
            {"album": "B", "action": "locked"},
        ]
        with tempfile.TemporaryDirectory() as td:
            cfgfile, cfg, user = self._user_cfg(td, rules)
            result = iaa.sync_user(cfg, user, config_file=cfgfile, verbose=False)
            self.assertEqual(result.conflicts, 1)
            self.assertEqual(result.changed, 1)
            self.assertEqual(FakeImmich.updates, [((FakeImmich.asset_a,), "locked")])

    def test_remove_rule_semantics_no_rules_means_no_changes(self):
        FakeImmich.assets[(FakeImmich.album_a, "timeline")] = [(FakeImmich.asset_a, FakeImmich.key_user)]
        with tempfile.TemporaryDirectory() as td:
            cfgfile, cfg, user = self._user_cfg(td, [])
            result = iaa.sync_user(cfg, user, config_file=cfgfile, verbose=False)
            self.assertEqual(result.changed, 0)
            self.assertEqual(FakeImmich.updates, [])

    def test_zero_server_albums_prints_album_sync_guidance_once(self):
        FakeImmich.albums_response = []
        with tempfile.TemporaryDirectory() as td:
            cfgfile, cfg, user = self._user_cfg(td)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                result = iaa.sync_user(cfg, user, dry_run=True, config_file=cfgfile, verbose=True)
            output = buf.getvalue()
            self.assertTrue(result.no_server_albums)
            self.assertIn("No Immich server albums found", output)
            self.assertIn("Backup album synchronization", output)
            self.assertIn("Reorganize into album", output)
            self.assertNotIn("Screenshots -> Archive: not found", output)

    def test_show_detected_albums_marks_actions(self):
        FakeImmich.albums_response = [
            {"id": FakeImmich.album_a, "albumName": "Screenshots", "assetCount": 12},
            {"id": FakeImmich.album_b, "albumName": "Private", "assetCount": 3},
            {"id": "77777777-7777-4777-8777-777777777777", "albumName": "Camera", "assetCount": 300},
        ]
        rules = [
            {"album": "Screenshots", "action": "archive"},
            {"album": "Private", "action": "locked"},
        ]
        with tempfile.TemporaryDirectory() as td:
            cfgfile, cfg, user = self._user_cfg(td, rules)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                iaa.show_detected_albums(cfg, user, cfgfile)
            output = buf.getvalue()
            self.assertIn("[ARCH] Screenshots (12 assets)", output)
            self.assertIn("[LOCK] Private (3 assets)", output)
            self.assertIn("[----] Camera (300 assets)", output)
            self.assertIn("Detected: 3 server albums", output)

    def test_key_storage_permissions(self):
        with tempfile.TemporaryDirectory() as td:
            cfgfile = Path(td) / "config.json"
            iaa.ensure_config(cfgfile)
            iaa.write_key(FakeImmich.key_user, "secret", cfgfile)
            path = iaa.key_path(FakeImmich.key_user, cfgfile)
            self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
            self.assertEqual(iaa.read_key(FakeImmich.key_user, cfgfile), "secret")


if __name__ == "__main__":
    unittest.main()
