import importlib.util
import json
import os
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
    archived = []
    key_user = "11111111-1111-4111-8111-111111111111"
    album_id = "22222222-2222-4222-8222-222222222222"
    asset_id = "33333333-3333-4333-8333-333333333333"
    albums_response = None

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
            return self._send(200, [{"id": self.album_id, "albumName": "Screenshots", "assetCount": 1}])
        return self._send(404, {})

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(size) or b"{}")
        if self.path == "/api/search/metadata":
            assert body["albumIds"] == [self.album_id]
            return self._send(200, {"assets": {"items": [
                {"id": self.asset_id, "ownerId": self.key_user},
                {"id": "44444444-4444-4444-8444-444444444444", "ownerId": "someone-else"},
            ], "nextPage": None}})
        return self._send(404, {})

    def do_PATCH(self):
        size = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(size) or b"{}")
        if self.path == "/api/assets":
            self.__class__.archived.extend(body["ids"])
            self.__class__.last_visibility = body["visibility"]
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
        FakeImmich.archived = []
        FakeImmich.albums_response = None

    def test_default_album_order(self):
        self.assertEqual(iaa.DEFAULT_ALBUMS, [
            "Screenshots", "Download", "WhatsApp", "WhatsApp Images",
            "WhatsApp Video", "Facebook", "Messenger", "Messages"
        ])

    def test_parse_users(self):
        text = """Initializing Immich v3\n[\n { id: '11111111-1111-4111-8111-111111111111', email: 'a@b', name: 'A', isAdmin: true, deletedAt: null, },\n]"""
        users = iaa._parse_immich_admin_users(text)
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0].name, "A")
        self.assertTrue(users[0].is_admin)

    def test_api_flow_and_shared_asset_protection(self):
        api = iaa.ImmichApi(self.base, "good-key")
        me = api.current_user()
        self.assertEqual(me["id"], FakeImmich.key_user)
        ids = api.timeline_asset_ids_for_album(FakeImmich.album_id, FakeImmich.key_user)
        self.assertEqual(ids, [FakeImmich.asset_id])
        count = api.archive_ids(ids)
        self.assertEqual(count, 1)
        self.assertEqual(FakeImmich.archived, [FakeImmich.asset_id])
        self.assertEqual(FakeImmich.last_visibility, "archive")


    def _user_cfg(self, td):
        cfgfile = Path(td) / "config.json"
        cfg = iaa.ensure_config(cfgfile)
        cfg["server_url"] = self.base
        cfg["users"] = {
            FakeImmich.key_user: {
                "name": "Jerry",
                "email": "j@example.test",
                "enabled": True,
                "albums": list(iaa.DEFAULT_ALBUMS),
            }
        }
        iaa.save_config(cfg, cfgfile)
        iaa.write_key(FakeImmich.key_user, "good-key", cfgfile)
        user = iaa.DiscoveredUser(FakeImmich.key_user, "j@example.test", "Jerry")
        return cfgfile, cfg, user

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
            self.assertNotIn("Screenshots: not found", output)

    def test_show_detected_albums_marks_auto_archive_targets(self):
        FakeImmich.albums_response = [
            {"id": FakeImmich.album_id, "albumName": "Screenshots", "assetCount": 12},
            {"id": "55555555-5555-4555-8555-555555555555", "albumName": "Camera", "assetCount": 300},
        ]
        with tempfile.TemporaryDirectory() as td:
            cfgfile, cfg, user = self._user_cfg(td)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                iaa.show_detected_albums(cfg, user, cfgfile)
            output = buf.getvalue()
            self.assertIn("[AUTO] Screenshots (12 assets)", output)
            self.assertIn("[----] Camera (300 assets)", output)
            self.assertIn("Detected: 2 server albums", output)

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
