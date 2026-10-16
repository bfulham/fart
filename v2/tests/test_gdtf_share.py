import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fart import gdtf_share
from fart.gdtf_share import GDTFShareClient, GDTFShareError


class FakeResponse:
    def __init__(self, raw):
        self._raw = raw

    def read(self):
        return self._raw


def http_error(code, payload):
    return urllib.error.HTTPError(
        url="https://gdtf-share.com/apis/public/x", code=code, msg="error",
        hdrs=None, fp=io.BytesIO(json.dumps(payload).encode("utf-8")))


class FakeOpener:
    def __init__(self, outcomes):
        # outcomes: list of callables (request) -> response or raises
        self._outcomes = list(outcomes)
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class GDTFShareClientTests(unittest.TestCase):
    def test_login_success_sets_logged_in_and_username(self):
        opener = FakeOpener([FakeResponse(json.dumps({"result": True, "notice": "hi"}).encode())])
        client = GDTFShareClient(opener=opener)
        client.login("alice", "hunter2")
        self.assertTrue(client.logged_in)
        self.assertEqual(client.username, "alice")

    def test_login_failure_raises_and_does_not_mark_logged_in(self):
        opener = FakeOpener([http_error(401, {"result": False, "error": "No valid user or password provided."})])
        client = GDTFShareClient(opener=opener)
        with self.assertRaises(GDTFShareError) as ctx:
            client.login("alice", "wrong")
        self.assertIn("No valid user", str(ctx.exception))
        self.assertFalse(client.logged_in)

    def test_get_list_unwraps_the_result_list_envelope(self):
        # The real API (verified live) wraps the catalog as
        # {"result": true, "list": [...]}, not a bare array.
        catalog = [{"rid": 1, "manufacturer": "Robe", "fixture": "MegaPointe", "modes": []}]
        opener = FakeOpener([FakeResponse(json.dumps({"result": True, "list": catalog}).encode())])
        client = GDTFShareClient(opener=opener)
        self.assertEqual(client.get_list(), catalog)

    def test_get_list_with_result_false_raises(self):
        opener = FakeOpener([FakeResponse(json.dumps({"result": False, "error": "Session expired."}).encode())])
        client = GDTFShareClient(opener=opener)
        with self.assertRaises(GDTFShareError) as ctx:
            client.get_list()
        self.assertIn("Session expired", str(ctx.exception))

    def test_get_list_unauthorized_raises(self):
        opener = FakeOpener([http_error(401, {"result": False, "error": "Unauthorized."})])
        client = GDTFShareClient(opener=opener)
        with self.assertRaises(GDTFShareError):
            client.get_list()

    def test_download_file_returns_raw_bytes(self):
        payload = b"PK\x03\x04not-really-a-zip-but-bytes-are-bytes"
        opener = FakeOpener([FakeResponse(payload)])
        client = GDTFShareClient(opener=opener)
        self.assertEqual(client.download_file(42), payload)

    def test_download_file_empty_response_raises(self):
        opener = FakeOpener([FakeResponse(b"")])
        client = GDTFShareClient(opener=opener)
        with self.assertRaises(GDTFShareError):
            client.download_file(42)


class FakeKeyringBackend:
    def __init__(self):
        self.store = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        del self.store[(service, username)]


class KeychainHelperTests(unittest.TestCase):
    def test_keychain_available_reflects_backend_presence(self):
        with patch("fart.gdtf_share._keyring", return_value=FakeKeyringBackend()):
            self.assertTrue(gdtf_share.keychain_available())
        with patch("fart.gdtf_share._keyring", return_value=None):
            self.assertFalse(gdtf_share.keychain_available())

    def test_store_and_load_password_round_trip(self):
        backend = FakeKeyringBackend()
        with patch("fart.gdtf_share._keyring", return_value=backend):
            self.assertTrue(gdtf_share.store_password("alice", "hunter2"))
            self.assertEqual(gdtf_share.load_stored_password("alice"), "hunter2")

    def test_load_stored_password_without_backend_returns_none(self):
        with patch("fart.gdtf_share._keyring", return_value=None):
            self.assertFalse(gdtf_share.store_password("alice", "hunter2"))
            self.assertIsNone(gdtf_share.load_stored_password("alice"))

    def test_forget_credentials_removes_keychain_entry_and_account_file(self):
        backend = FakeKeyringBackend()
        backend.set_password(gdtf_share.KEYRING_SERVICE, "alice", "hunter2")
        with self._patched_account_file() as account_file:
            account_file.write_text(json.dumps({"username": "alice"}))
            with patch("fart.gdtf_share._keyring", return_value=backend):
                gdtf_share.forget_credentials("alice")
            self.assertFalse(account_file.exists())
            self.assertIsNone(backend.get_password(gdtf_share.KEYRING_SERVICE, "alice"))

    def test_remember_and_get_username_round_trip(self):
        with self._patched_account_file() as account_file:
            self.assertIsNone(gdtf_share.get_remembered_username())
            gdtf_share.remember_username("bob")
            self.assertEqual(gdtf_share.get_remembered_username(), "bob")
            self.assertTrue(account_file.exists())

    def _patched_account_file(self):
        import tempfile
        tmp_dir = tempfile.mkdtemp()
        account_file = Path(tmp_dir) / "account.json"
        return patch("fart.gdtf_share.ACCOUNT_FILE", account_file)


if __name__ == "__main__":
    unittest.main()
