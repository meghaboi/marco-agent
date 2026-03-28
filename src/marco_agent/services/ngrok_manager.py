from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(slots=True)
class NgrokTunnelSession:
    public_url: str
    expires_at: datetime
    process_pid: int | None
    local_port: int


class NgrokTunnelManager:
    def __init__(self, *, binary: str, auth_token: str | None, max_ttl_minutes: int, api_url: str) -> None:
        self._binary = binary.strip() or "ngrok"
        self._auth_token = (auth_token or "").strip()
        self._max_ttl = max(1, int(max_ttl_minutes))
        self._api_url = api_url.rstrip("/")
        self._session: NgrokTunnelSession | None = None
        self._proc: subprocess.Popen[str] | None = None

    def open_tunnel(self, *, local_port: int, ttl_minutes: int = 120) -> dict[str, str]:
        ttl = min(max(1, int(ttl_minutes)), self._max_ttl)
        if not self._auth_token:
            return {"ok": "false", "error": "NGROK_AUTH_TOKEN missing."}
        if self._session and not self._is_expired(self._session):
            return {
                "ok": "true",
                "public_url": self._session.public_url,
                "expires_at": self._session.expires_at.isoformat(),
                "reused": "true",
            }
        try:
            subprocess.run(
                [self._binary, "config", "add-authtoken", self._auth_token],
                capture_output=True,
                text=True,
                check=False,
            )
            self._proc = subprocess.Popen(
                [self._binary, "http", str(local_port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except Exception as exc:
            return {"ok": "false", "error": str(exc)}

        public_url = self._resolve_public_url(local_port=local_port)
        if not public_url:
            self.close_tunnel()
            return {"ok": "false", "error": "Failed to resolve ngrok public URL from local API."}
        expires_at = datetime.now(UTC) + timedelta(minutes=ttl)
        self._session = NgrokTunnelSession(
            public_url=public_url,
            expires_at=expires_at,
            process_pid=self._proc.pid,
            local_port=local_port,
        )
        return {"ok": "true", "public_url": public_url, "expires_at": expires_at.isoformat()}

    def close_tunnel(self) -> dict[str, str]:
        if self._proc:
            self._proc.terminate()
        self._proc = None
        self._session = None
        return {"ok": "true"}

    def get_status(self) -> dict[str, str]:
        if not self._session:
            return {"ok": "true", "active": "false"}
        if self._is_expired(self._session):
            self.close_tunnel()
            return {"ok": "true", "active": "false", "expired": "true"}
        return {
            "ok": "true",
            "active": "true",
            "public_url": self._session.public_url,
            "local_port": str(self._session.local_port),
            "expires_at": self._session.expires_at.isoformat(),
        }

    @staticmethod
    def _is_expired(session: NgrokTunnelSession) -> bool:
        return datetime.now(UTC) >= session.expires_at

    def _resolve_public_url(self, *, local_port: int) -> str | None:
        for _ in range(20):
            response = _http_get_json(url=f"{self._api_url}/api/tunnels")
            if not isinstance(response, dict):
                time.sleep(0.25)
                continue
            tunnels = response.get("tunnels")
            if not isinstance(tunnels, list):
                time.sleep(0.25)
                continue
            for tunnel in tunnels:
                if not isinstance(tunnel, dict):
                    continue
                public_url = str(tunnel.get("public_url", "")).strip()
                config = tunnel.get("config")
                if not isinstance(config, dict):
                    continue
                addr = str(config.get("addr", "")).strip().lower()
                if not public_url.startswith("https://"):
                    continue
                if addr.endswith(f":{local_port}") or addr == str(local_port):
                    return public_url
            time.sleep(0.25)
        return None


def _http_get_json(*, url: str) -> dict | None:
    req = urllib.request.Request(url=url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            raw = response.read().decode("utf-8", errors="ignore")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None
