"""Minimalny klient MCP (streamable HTTP) dla czyjesteldorado.pl.

Serwer jest w bardzo wczesnej becie, wiec klient zaklada, ze wszystko moze
paść: sesja wygasa, serwer zwraca 429/5xx, odpowiedz bywa SSE zamiast JSON.
Cala ta obsluga siedzi tutaj, zeby warstwa zbierania danych byla czysta.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from typing import Any

ENDPOINT = "https://czyjesteldorado.pl/_mcp"
PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "it-market-radar", "version": "0.1.0"}


class MCPError(RuntimeError):
    """Serwer odpowiedzial, ale trescia bledu JSON-RPC."""


class SessionExpired(RuntimeError):
    """Serwer nie zna juz naszego mcp-session-id - trzeba zrobic initialize."""


def _parse_body(raw: bytes, content_type: str) -> dict[str, Any]:
    """Zwraca ciało JSON-RPC niezaleznie od tego, czy przyszlo jako JSON czy SSE."""
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return {}
    if "text/event-stream" in content_type:
        # W strumieniu SSE interesuja nas tylko linie "data:".
        for line in text.splitlines():
            if line.startswith("data:"):
                payload = line[len("data:"):].strip()
                if payload:
                    return json.loads(payload)
        return {}
    return json.loads(text)


class EldoradoClient:
    """Klient trzymajacy jedna sesje MCP i wolajacy narzedzia serwera.

    Uzycie:
        with EldoradoClient() as client:
            wynik = client.search_jobs(phrase="React", seniority=["junior"])
    """

    def __init__(
        self,
        endpoint: str = ENDPOINT,
        *,
        min_interval: float = 1.0,
        max_retries: int = 4,
        timeout: float = 45.0,
    ) -> None:
        self.endpoint = endpoint
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self._session_id: str | None = None
        self._next_id = 0
        self._last_call_at = 0.0
        self.server_info: dict[str, Any] = {}

    # --- cykl zycia -------------------------------------------------------

    def __enter__(self) -> "EldoradoClient":
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self._session_id = None

    def connect(self) -> dict[str, Any]:
        """Wykonuje handshake initialize + notifications/initialized."""
        self._session_id = None
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
            allow_reconnect=False,
        )
        self.server_info = result.get("serverInfo", {})
        self._notify("notifications/initialized")
        return result

    # --- warstwa transportowa ---------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "it-market-radar/0.1 (+hobby project, kontakt przez GitHub)",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
            headers["Mcp-Protocol-Version"] = PROTOCOL_VERSION
        return headers

    def _throttle(self) -> None:
        """Nie wiecej niz jedno zapytanie na min_interval sekund."""
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call_at = time.monotonic()

    def _post(self, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        self._throttle()
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            headers = {k.lower(): v for k, v in response.headers.items()}
            body = _parse_body(response.read(), headers.get("content-type", ""))
            return body, headers

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._post(payload)

    def _rpc(
        self,
        method: str,
        params: dict[str, Any],
        *,
        allow_reconnect: bool = True,
    ) -> dict[str, Any]:
        """Wola metode JSON-RPC z ponawianiem i odtwarzaniem sesji."""
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
            "params": params,
        }

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                body, headers = self._post(payload)
            except urllib.error.HTTPError as exc:
                # 404 na istniejacej sesji = serwer o niej zapomnial.
                if exc.code == 404 and self._session_id and allow_reconnect:
                    self.connect()
                    continue
                if exc.code in (408, 425, 429, 500, 502, 503, 504):
                    last_error = exc
                    self._backoff(attempt)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                self._backoff(attempt)
                continue

            if sid := headers.get("mcp-session-id"):
                self._session_id = sid
            if error := body.get("error"):
                raise MCPError(f"{method}: {error}")
            return body.get("result", {})

        raise RuntimeError(f"{method} nie powiodlo sie po {self.max_retries} probach") from last_error

    def _backoff(self, attempt: int) -> None:
        """Wykladniczy backoff z jitterem, zeby nie dobijac sie rowno co sekunde."""
        time.sleep(min(2**attempt, 30) + random.uniform(0, 0.5))

    # --- narzedzia serwera -------------------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        return self._rpc("tools/list", {}).get("tools", [])

    def search_jobs(self, **arguments: Any) -> dict[str, Any]:
        """Wola search_jobs i rozpakowuje JSON zaszyty w tekstowej odpowiedzi.

        Serwer zwraca wynik jako content[0].text bedacy stringiem z JSON-em,
        wiec parsujemy go dwa razy. Zwracamy dict z kluczami:
        jobs, shown_results, total_results.
        """
        if not self._session_id:
            self.connect()

        result = self._rpc("tools/call", {"name": "search_jobs", "arguments": arguments})
        blocks = result.get("content", [])
        for block in blocks:
            if block.get("type") == "text":
                return json.loads(block["text"])
        return {"jobs": [], "shown_results": 0, "total_results": 0}
