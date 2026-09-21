import os
from typing import Any

import httpx


class BackendAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class BackendClient:
    def __init__(self):
        self.base_url = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
        self.token = os.getenv("INTERNAL_API_TOKEN", "")

    def _headers(self) -> dict[str, str]:
        return {"X-Internal-Token": self.token, "Content-Type": "application/json"}

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        try:
            data = response.json()
            if isinstance(data, dict) and data.get("detail"):
                return str(data["detail"])
        except Exception:
            pass
        text = (response.text or "").strip()
        # Django DEBUG HTML pages are useless in Telegram; keep a short summary.
        if text.lower().startswith("<!doctype") or "<html" in text[:200].lower():
            return f"HTTP {response.status_code}"
        return text[:300] or f"HTTP {response.status_code}"

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        with httpx.Client(timeout=30.0) as client:
            response = client.request(method, url, headers=self._headers(), **kwargs)
        if response.status_code >= 400:
            raise BackendAPIError(self._error_detail(response), status_code=response.status_code)
        if response.status_code == 204:
            return None
        return response.json()

    def upsert_user(
        self,
        telegram_id: int,
        username: str = "",
        first_name: str = "",
        language_code: str = "ru",
    ) -> dict:
        return self._request(
            "POST",
            "/api/v1/bot/users/upsert",
            json={
                "telegram_id": telegram_id,
                "username": username or "",
                "first_name": first_name or "",
                "language_code": language_code or "ru",
            },
        )

    def get_user(self, telegram_id: int) -> dict:
        return self._request("GET", f"/api/v1/bot/users/{telegram_id}")

    def connect(self, telegram_id: int, api_key: str) -> dict:
        return self._request(
            "POST",
            f"/api/v1/bot/users/{telegram_id}/connect",
            json={"api_key": api_key},
        )

    def disconnect(self, telegram_id: int) -> None:
        self._request("DELETE", f"/api/v1/bot/users/{telegram_id}/connect")

    def get_plan(self, telegram_id: int, refresh: bool = True) -> dict:
        q = "?refresh=1" if refresh else ""
        return self._request("GET", f"/api/v1/bot/users/{telegram_id}/plan{q}")

    def get_form(self, telegram_id: int, refresh: bool = True) -> dict:
        q = "?refresh=1" if refresh else ""
        return self._request("GET", f"/api/v1/bot/users/{telegram_id}/form{q}")

    def get_zones(self, telegram_id: int, refresh: bool = True) -> dict:
        q = "?refresh=1" if refresh else ""
        return self._request("GET", f"/api/v1/bot/users/{telegram_id}/zones{q}")

    def get_recent_reports(self, telegram_id: int, limit: int = 5) -> dict:
        return self._request(
            "GET", f"/api/v1/bot/users/{telegram_id}/reports/recent?limit={limit}"
        )

    def download_media(self, rel_path: str) -> bytes:
        url = f"{self.base_url}/api/v1/bot/media/{rel_path}"
        with httpx.Client(timeout=60.0) as client:
            response = client.get(url, headers=self._headers())
        if response.status_code >= 400:
            raise BackendAPIError(
                f"Media download failed: {response.status_code}",
                status_code=response.status_code,
            )
        return response.content

    def get_settings(self, telegram_id: int) -> dict:
        return self._request("GET", f"/api/v1/bot/users/{telegram_id}/settings")

    def patch_settings(self, telegram_id: int, payload: dict) -> dict:
        return self._request(
            "PATCH", f"/api/v1/bot/users/{telegram_id}/settings", json=payload
        )

    def request_analyze(self, telegram_id: int, kind: str = "day", date: str | None = None) -> dict:
        payload: dict[str, Any] = {"kind": kind}
        if date:
            payload["date"] = date
        return self._request(
            "POST", f"/api/v1/bot/users/{telegram_id}/analyze", json=payload
        )
