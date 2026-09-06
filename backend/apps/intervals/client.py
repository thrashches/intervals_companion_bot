import logging
import time
from typing import Any

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


class IntervalsAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class IntervalsClient:
    """HTTP client for intervals.icu personal API key auth."""

    def __init__(
        self,
        api_key: str,
        athlete_id: str = "0",
        base_url: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
    ):
        self.api_key = api_key
        self.athlete_id = athlete_id or "0"
        self.base_url = (base_url or settings.INTERVALS_API_BASE).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        auth = ("API_KEY", self.api_key)
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout, auth=auth) as client:
                    response = client.request(method, url, **kwargs)
                if response.status_code in (429, 500, 502, 503, 504):
                    raise IntervalsAPIError(
                        f"Transient error {response.status_code}",
                        status_code=response.status_code,
                    )
                if response.status_code == 401:
                    raise IntervalsAPIError("Invalid API key", status_code=401)
                if response.status_code >= 400:
                    raise IntervalsAPIError(
                        f"API error {response.status_code}: {response.text[:300]}",
                        status_code=response.status_code,
                    )
                if response.status_code == 204 or not response.content:
                    return None
                return response.json()
            except (httpx.HTTPError, IntervalsAPIError) as exc:
                last_error = exc
                retryable = isinstance(exc, httpx.HTTPError) or (
                    isinstance(exc, IntervalsAPIError)
                    and exc.status_code in (429, 500, 502, 503, 504)
                )
                if not retryable or attempt == self.max_retries:
                    break
                sleep_for = min(2 ** attempt, 8)
                logger.warning(
                    "intervals.icu request failed (%s), retry in %ss",
                    type(exc).__name__,
                    sleep_for,
                )
                time.sleep(sleep_for)

        if isinstance(last_error, IntervalsAPIError):
            raise last_error
        raise IntervalsAPIError(str(last_error or "Unknown error"))

    def get_athlete(self) -> dict:
        return self._request("GET", f"/athlete/{self.athlete_id}")

    def get_events(self, oldest: str, newest: str, resolve: bool = True) -> list:
        params = {"oldest": oldest, "newest": newest}
        if resolve:
            params["resolve"] = "true"
        data = self._request("GET", f"/athlete/{self.athlete_id}/events", params=params)
        return data or []

    def get_wellness(self, oldest: str, newest: str) -> list:
        data = self._request(
            "GET",
            f"/athlete/{self.athlete_id}/wellness",
            params={"oldest": oldest, "newest": newest},
        )
        return data or []

    def get_fitness(self, oldest: str, newest: str) -> list:
        data = self._request(
            "GET",
            f"/athlete/{self.athlete_id}/fitness",
            params={"oldest": oldest, "newest": newest},
        )
        return data or []

    def get_activities(self, oldest: str, newest: str) -> list:
        data = self._request(
            "GET",
            f"/athlete/{self.athlete_id}/activities",
            params={"oldest": oldest, "newest": newest},
        )
        return data or []

    def get_activity(self, activity_id: str, intervals: bool = True) -> dict:
        params = {"intervals": "true"} if intervals else None
        return self._request("GET", f"/activity/{activity_id}", params=params)
