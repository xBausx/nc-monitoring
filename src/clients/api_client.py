import os
import json
import logging
from typing import Any, Dict, Optional, Union, Mapping

import requests

logger = logging.getLogger(__name__)


class APIClient:
    """
    Minimal N-Compass API client.

    - Handles login and token storage.
    - Exposes generic request helper plus a couple of convenience methods.
    - Does NOT assume any particular response shape, just returns response.json().
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        token_file: str = "tokens.json",
    ) -> None:
        self.base_url = (base_url or os.getenv("NC_API_BASE_URL", "")).rstrip("/")
        self.username = username or os.getenv("NC_API_USERNAME", "")
        self.password = password or os.getenv("NC_API_PASSWORD", "")
        self.token_file = token_file

        self.session = requests.Session()
        self.token: Optional[str] = None
        self.refresh_token: Optional[str] = None

        self._load_tokens()

        if self.token:
            self.session.headers.update({"Authorization": f"Bearer {self.token}"})

        if not self.base_url:
            logger.warning(
                "APIClient initialized without base_url. "
                "Set NC_API_BASE_URL env var or pass base_url explicitly."
            )

    # --------------------------------------------------------------------- #
    # Token persistence helpers
    # --------------------------------------------------------------------- #

    def _load_tokens(self) -> None:
        """Try to load token and refresh token from disk."""
        if not os.path.exists(self.token_file):
            return

        try:
            with open(self.token_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.token = data.get("token")
            self.refresh_token = data.get("refreshToken")
            logger.info("Loaded tokens from %s", self.token_file)
        except Exception as exc:
            logger.warning("Failed to load tokens from %s: %s", self.token_file, exc)

    def _save_tokens(self) -> None:
        """Persist token and refresh token to disk for reuse."""
        if not self.token:
            return

        data = {"token": self.token, "refreshToken": self.refresh_token}
        try:
            with open(self.token_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
            logger.info("Saved tokens to %s", self.token_file)
        except Exception as exc:
            logger.warning("Failed to save tokens to %s: %s", self.token_file, exc)

    # --------------------------------------------------------------------- #
    # Authentication
    # --------------------------------------------------------------------- #

    def login(self, username: Optional[str] = None, password: Optional[str] = None) -> bool:
        """
        Log in to the API and store token/refreshToken.

        Uses:
        - passed username/password if provided, otherwise
        - ones from __init__ (env or explicit).
        """
        user = username or self.username
        pwd = password or self.password

        if not self.base_url:
            logger.error("Cannot login: base_url is not set.")
            return False

        if not user or not pwd:
            logger.error("Cannot login: username or password missing.")
            return False

        login_url = f"{self.base_url}/api/account/login"
        payload = {"username": user, "password": pwd}

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
        }

        logger.info("Logging in to %s as %s", login_url, user)

        try:
            resp = self.session.post(login_url, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Login request failed: %s", exc)
            return False

        try:
            data = resp.json()
        except ValueError as exc:
            logger.error("Failed to parse login response JSON: %s", exc)
            return False

        token = data.get("token")
        refresh = data.get("refreshToken")

        if not token:
            logger.error("Login failed: no token in response.")
            return False

        self.token = token
        self.refresh_token = refresh
        self.session.headers.update({"Authorization": f"Bearer {self.token}"})
        self._save_tokens()

        logger.info("Login successful.")
        return True

    def _ensure_auth(self) -> bool:
        """
        Make sure we have a valid auth context.

        Some NC API endpoints (e.g. /api/license/getall) expect an auth cookie
        on the session in addition to the Bearer token. If we only restore the
        token from tokens.json and never perform a fresh login(), the session
        will have no cookies and those endpoints respond with
        {"message": "cookie is required!"}.

        To keep things robust we treat BOTH token and cookies as required.
        If either is missing we trigger a fresh login() so the session picks up
        the token and the cookie again.
        """
        has_token = bool(self.token)
        has_cookies = bool(self.session.cookies)

        if has_token and has_cookies:
            return True

        logger.info(
            "Auth context incomplete (token=%s, cookies=%s); attempting login...",
            "yes" if has_token else "no",
            "yes" if has_cookies else "no",
        )
        return self.login()


    # --------------------------------------------------------------------- #
    # Generic request helper
    # --------------------------------------------------------------------- #

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        retry_on_401: bool = True,
    ) -> Optional[Union[Dict[str, Any], Any]]:
        """
        Internal helper to make an authenticated request and return response.json().

        Returns:
            Parsed JSON (dict/list/whatever response is), or None on error.
        """
        if not self._ensure_auth():
            return None

        if not self.base_url:
            logger.error("Cannot make request: base_url is not set.")
            return None

        url = f"{self.base_url}{path}"

        try:
            resp = self.session.request(
                method=method.upper(),
                url=url,
                params=params,
                json=json_body,
                timeout=30,
            )
        except requests.RequestException as exc:
            logger.error("Request to %s failed: %s", url, exc)
            return None

        # Handle unauthorized – try a single re-login & retry
        if resp.status_code == 401 and retry_on_401:
            logger.warning("Received 401 from %s, retrying after re-login...", url)
            self.token = None
            self.session.headers.pop("Authorization", None)

            if not self.login():
                logger.error("Re-login failed; giving up on %s", url)
                return None

            return self._request(
                method,
                path,
                params=params,
                json_body=json_body,
                retry_on_401=False,
            )

        if not resp.ok:
            logger.error(
                "Request to %s failed: %s %s - %s",
                url,
                resp.status_code,
                resp.reason,
                resp.text[:500],
            )
            return None

        if resp.content == b"":
            return None

        try:
            return resp.json()
        except ValueError:
            # Not JSON – return raw text as a fallback
            logger.debug("Non-JSON response from %s, returning text.", url)
            return resp.text

    # --------------------------------------------------------------------- #
    # Convenience methods
    # --------------------------------------------------------------------- #

    def get_licenses(self, params: Optional[Dict[str, Any]] = None) -> Optional[Any]:
        """
        Fetch licenses from /api/license/getall.

        `params` is passed straight through to the API. You can supply any filters
        you need (page, pageSize, piStatus, active, assigned, timezone, etc.).
        """
        return self._request("GET", "/api/license/getall", params=params)

    def get_screenshots(self, license_id: str) -> Optional[Any]:
        """
        Fetch screenshot files for a specific license from /api/pi/getfiles.

        This returns whatever JSON the API responds with.
        """
        if not license_id:
            logger.error("get_screenshots called without license_id")
            return None

        params = {"licenseid": license_id}
        return self._request("GET", "/api/pi/getfiles", params=params)
