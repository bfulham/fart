"""GDTF Share (gdtf-share.com) REST client and credential storage.

The real API has no anonymous mode -- every endpoint, including listing
the catalog, requires a logged-in session (verified live: getList.php
returns 401 Unauthorized with no session cookie). So there is no way to
defer login past "the first API call this run actually needs to make".
What *can* be deferred is showing the UI: a previously cached catalog
listing (see gdtf_share_cache in ui/gdtf_share_dialog.py) can be browsed
with no login at all, pushing the real login prompt out to whenever the
user clicks Refresh or Import -- the closest this API allows to the
"login only when required" behaviour asked for.

This module is deliberately Qt-free so it can be unit tested without a
QApplication and without touching the real network or the real OS
keychain: GDTFShareClient takes an injectable `opener` (anything with an
`.open(request) -> response` method, matching urllib's contract), and
the keychain helpers go through a `_keyring` indirection tests can patch.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

try:
    import keyring
except Exception:  # pragma: no cover - keyring should always be installed
    keyring = None

BASE_URL = "https://gdtf-share.com/apis/public/"
KEYRING_SERVICE = "FART GDTF Share"
ACCOUNT_FILE = Path.home() / "FART2_gdtf_share.json"


class GDTFShareError(Exception):
    pass


def _read_json_response(response):
    raw = response.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise GDTFShareError(f"GDTF Share returned an unreadable response: {exc}") from exc


class GDTFShareClient:
    """One logged-in session against the GDTF Share API.

    Not thread-safe; create one instance per use (e.g. per dialog) rather
    than sharing across the app -- there's no benefit to reuse since a
    session is only good for the lifetime of one browse/import action.
    """

    def __init__(self, opener=None):
        self._cookies = CookieJar()
        self._opener = opener or urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._cookies))
        self.logged_in = False
        self.username = None

    def _request(self, path, method="GET", body=None):
        url = BASE_URL + path
        headers = {}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            return self._opener.open(request, timeout=15)
        except urllib.error.HTTPError as exc:
            payload = _read_json_response(exc)
            raise GDTFShareError(payload.get("error") or f"GDTF Share request failed ({exc.code})")
        except urllib.error.URLError as exc:
            raise GDTFShareError(f"Could not reach GDTF Share: {exc.reason}") from exc

    def login(self, username, password):
        response = self._request("login.php", method="POST", body={"user": username, "password": password})
        payload = _read_json_response(response)
        if not payload.get("result"):
            raise GDTFShareError(payload.get("error") or "Login failed.")
        self.logged_in = True
        self.username = username

    def get_list(self):
        response = self._request("getList.php")
        payload = _read_json_response(response)
        if not isinstance(payload, dict) or not payload.get("result"):
            error = payload.get("error") if isinstance(payload, dict) else None
            raise GDTFShareError(error or "Could not list GDTF Share fixtures.")
        return payload.get("list", [])

    def download_file(self, rid):
        response = self._request(f"downloadFile.php?rid={rid}")
        data = response.read()
        if not data:
            raise GDTFShareError("GDTF Share returned an empty file.")
        return data


def _keyring():
    if keyring is None:
        return None
    try:
        backend = keyring.get_keyring()
        # The "fail" backend is keyring's own stand-in for "no real backend
        # is available on this system" -- treat it the same as not having
        # keyring installed at all, rather than letting it silently raise
        # later when actually used.
        if type(backend).__module__.endswith("backends.fail"):
            return None
        return keyring
    except Exception:
        return None


def keychain_available():
    return _keyring() is not None


def get_remembered_username():
    try:
        if ACCOUNT_FILE.exists():
            data = json.loads(ACCOUNT_FILE.read_text())
            return data.get("username") or None
    except Exception:
        return None
    return None


def remember_username(username):
    try:
        ACCOUNT_FILE.write_text(json.dumps({"username": username}))
    except Exception:
        pass


def load_stored_password(username):
    kr = _keyring()
    if kr is None or not username:
        return None
    try:
        return kr.get_password(KEYRING_SERVICE, username)
    except Exception:
        return None


def store_password(username, password):
    kr = _keyring()
    if kr is None or not username:
        return False
    try:
        kr.set_password(KEYRING_SERVICE, username, password)
        return True
    except Exception:
        return False


def forget_credentials(username):
    kr = _keyring()
    if kr is not None and username:
        try:
            kr.delete_password(KEYRING_SERVICE, username)
        except Exception:
            pass
    try:
        if ACCOUNT_FILE.exists():
            ACCOUNT_FILE.unlink()
    except Exception:
        pass
