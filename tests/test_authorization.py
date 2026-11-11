from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from atomdeeplit import authorization, zotero


class AuthorizationTests(unittest.TestCase):
    def test_remembered_authorization_is_reused_for_same_server(self):
        stamp = ("sample-server", "5")
        key = "A" * 32
        with TemporaryDirectory() as directory, \
                patch.object(authorization, "CACHE_DIR", Path(directory)), \
                patch.object(zotero, "identity", return_value=stamp), \
                patch.object(zotero, "request", side_effect=[
                    ({"key": key, "remember": True}, {}),
                    ({"successful": {}}, {}),
                    ({"successful": {}}, {}),
                ]) as request, redirect_stdout(io.StringIO()):
            zotero.create_items(stamp, [])
            zotero.create_items(stamp, [])
            self.assertEqual([call.args[0] for call in request.call_args_list], [
                "local/authorize", "users/0/items", "users/0/items",
            ])
            self.assertEqual(request.call_args.args[2]["Zotero-API-Key"], key)
            self.assertIsNone(authorization.load("another-server"))
            path = authorization.cache_path(stamp[0])
            if os.name == "nt":
                self.assertNotIn(key.encode(), path.read_bytes())
            else:
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_single_use_authorization_is_not_saved(self):
        stamp = ("sample-server", "5")
        with patch.object(authorization, "load", return_value=None), \
                patch.object(authorization, "save") as save, \
                patch.object(zotero, "identity", return_value=stamp), \
                patch.object(zotero, "request", side_effect=[
                    ({"key": "B" * 32, "remember": False}, {}),
                    ({"successful": {}}, {}),
                ]), redirect_stdout(io.StringIO()):
            zotero.create_items(stamp, [])
            save.assert_not_called()

    def test_remembered_grant_is_saved_even_if_library_changes(self):
        stamp = ("sample-server", "5")
        with patch.object(authorization, "load", return_value=None), \
                patch.object(authorization, "save") as save, \
                patch.object(zotero, "identity", return_value=("sample-server", "6")), \
                patch.object(zotero, "request", return_value=({"key": "A" * 32, "remember": True}, {})) as request, \
                redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "changed while waiting"):
                zotero.create_items(stamp, [])
            request.assert_called_once()
            self.assertIsNone(request.call_args.kwargs["timeout"])
            save.assert_called_once_with(stamp[0], "A" * 32)

    def test_revoked_key_requests_one_new_grant(self):
        stamp = ("sample-server", "5")
        revoked = HTTPError("http://127.0.0.1", 401, "Unauthorized",
                            {"WWW-Authenticate": 'Zotero-API-Key realm="Zotero Local API"'}, None)
        with patch.object(authorization, "load", return_value="A" * 32), \
                patch.object(authorization, "save") as save, \
                patch.object(zotero, "identity", return_value=stamp), \
                patch.object(zotero, "request", side_effect=[
                    revoked, ({"key": "B" * 32, "remember": True}, {}), ({"successful": {}}, {}),
                ]) as request, redirect_stdout(io.StringIO()):
            zotero.create_items(stamp, [])
            self.assertEqual([call.args[0] for call in request.call_args_list], [
                "users/0/items", "local/authorize", "users/0/items",
            ])
            save.assert_called_once_with(stamp[0], "B" * 32)

    def test_conflict_does_not_retry_or_open_dialog(self):
        conflict = HTTPError("http://127.0.0.1", 412, "Precondition Failed", {}, None)
        with patch.object(authorization, "load", return_value="A" * 32), \
                patch.object(zotero, "request", side_effect=conflict) as request:
            try:
                with self.assertRaises(HTTPError):
                    zotero.create_items(("sample-server", "5"), [])
            finally:
                conflict.close()
            request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
