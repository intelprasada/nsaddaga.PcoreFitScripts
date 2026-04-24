"""Tiny HTTP client for the VegaNotes REST API (stdlib only)."""

from __future__ import annotations

import base64
import json
from typing import Any, Optional
from urllib import error, parse, request

from .config import Credentials


class ApiError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


class Client:
    def __init__(self, creds: Credentials, timeout: float = 30.0):
        self.creds = creds
        self.timeout = timeout
        token = base64.b64encode(f"{creds.user}:{creds.password}".encode()).decode()
        self._auth_header = f"Basic {token}"

    # ---- low level ---------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        body: Optional[Any] = None,
    ) -> Any:
        url = self.creds.url + path
        if params:
            flat: list[tuple[str, str]] = []
            for k, v in params.items():
                if v is None:
                    continue
                if isinstance(v, (list, tuple)):
                    for item in v:
                        flat.append((k, str(item)))
                elif isinstance(v, bool):
                    flat.append((k, "true" if v else "false"))
                else:
                    flat.append((k, str(v)))
            if flat:
                url = url + "?" + parse.urlencode(flat)

        data: Optional[bytes] = None
        headers = {"Authorization": self._auth_header, "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = request.Request(url, data=data, method=method, headers=headers)
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except error.HTTPError as e:
            raise ApiError(e.code, e.read().decode("utf-8", "replace")) from e
        except error.URLError as e:
            raise ApiError(0, f"connection error: {e.reason}") from e

        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw.decode("utf-8", "replace")

    # ---- convenience -------------------------------------------------------

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params)

    def patch(self, path: str, body: dict[str, Any]) -> Any:
        return self.request("PATCH", path, body=body)

    def put(self, path: str, body: dict[str, Any]) -> Any:
        return self.request("PUT", path, body=body)

    def post(self, path: str, body: Optional[dict[str, Any]] = None) -> Any:
        return self.request("POST", path, body=body or {})

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)
