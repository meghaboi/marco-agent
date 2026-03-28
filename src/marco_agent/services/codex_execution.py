from __future__ import annotations

import secrets
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from marco_agent.services.secrets_provider import SecretProvider


@dataclass(slots=True)
class PendingCodexAuth:
    code: str
    expires_at: datetime


class CodexAuthSessionManager:
    def __init__(self, *, secret_provider: SecretProvider, default_ttl_minutes: int) -> None:
        self._secret_provider = secret_provider
        self._default_ttl = max(1, int(default_ttl_minutes))
        self._pending: dict[str, PendingCodexAuth] = {}

    def start_interactive_login(self, *, user_id: str) -> dict[str, str]:
        code = secrets.token_urlsafe(10)
        expires_at = datetime.now(UTC) + timedelta(minutes=10)
        self._pending[user_id] = PendingCodexAuth(code=code, expires_at=expires_at)
        return {
            "ok": "true",
            "verification_code": code,
            "expires_at": expires_at.isoformat(),
            "instructions": "Complete auth then call codex_auth_complete with verification_code.",
        }

    def complete_interactive_login(self, *, user_id: str, verification_code: str, token: str | None = None) -> dict[str, str]:
        pending = self._pending.get(user_id)
        if not pending:
            return {"ok": "false", "error": "No pending auth session."}
        if datetime.now(UTC) > pending.expires_at:
            self._pending.pop(user_id, None)
            return {"ok": "false", "error": "Auth session expired."}
        if verification_code.strip() != pending.code:
            return {"ok": "false", "error": "Invalid verification_code."}
        value = (token or "").strip()
        if value:
            self._secret_provider.set_secret(key=f"MARCO-CODEX-TOKEN-{user_id}", value=value)
        else:
            self._secret_provider.set_secret(
                key=f"MARCO-CODEX-TOKEN-{user_id}",
                value=f"device-auth-completed:{datetime.now(UTC).isoformat()}",
            )
        self._pending.pop(user_id, None)
        return {"ok": "true", "message": "Codex auth completed and persisted."}

    def get_token(self, *, user_id: str) -> str | None:
        return self._secret_provider.get_secret(key=f"MARCO-CODEX-TOKEN-{user_id}")


class ExecutionJobRunner:
    def __init__(
        self,
        *,
        aca_job_name: str | None,
        aca_resource_group: str | None,
        aci_resource_group: str | None,
        execute_commands: bool = True,
    ) -> None:
        self._aca_job_name = (aca_job_name or "").strip()
        self._aca_resource_group = (aca_resource_group or "").strip()
        self._aci_resource_group = (aci_resource_group or "").strip()
        self._execute = bool(execute_commands)

    def run(self, *, mode: str, image: str, command: list[str], env: dict[str, str] | None = None) -> dict[str, str]:
        if mode == "aca_job":
            if not (self._aca_job_name and self._aca_resource_group):
                return {"ok": "false", "error": "ACA job config missing."}
            cmd = [
                "az",
                "containerapp",
                "job",
                "start",
                "--name",
                self._aca_job_name,
                "--resource-group",
                self._aca_resource_group,
                "--output",
                "json",
            ]
            return _run_command(cmd=cmd, execute=self._execute)
        if mode == "aci":
            if not self._aci_resource_group:
                return {"ok": "false", "error": "ACI resource group missing."}
            safe_name = f"marco-job-{secrets.token_hex(3)}"
            cmd = [
                "az",
                "container",
                "create",
                "--resource-group",
                self._aci_resource_group,
                "--name",
                safe_name,
                "--image",
                image,
                "--restart-policy",
                "Never",
                "--output",
                "json",
            ]
            if command:
                cmd.extend(["--command-line", " ".join(command)])
            if env:
                env_rows = [f"{k}={v}" for k, v in env.items() if k.strip()]
                if env_rows:
                    cmd.extend(["--environment-variables", *env_rows])
            result = _run_command(cmd=cmd, execute=self._execute)
            if result.get("ok") == "true":
                result["container_name"] = safe_name
            return result
        return {"ok": "false", "error": f"Unsupported mode '{mode}'."}


def _run_command(*, cmd: list[str], execute: bool) -> dict[str, str]:
    rendered = " ".join(cmd)
    if not execute:
        return {"ok": "true", "dry_run": "true", "command": rendered}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except Exception as exc:
        return {"ok": "false", "error": str(exc), "command": rendered}
    return {
        "ok": "true" if proc.returncode == 0 else "false",
        "command": rendered,
        "code": str(proc.returncode),
        "stdout": (proc.stdout or "").strip()[:4000],
        "stderr": (proc.stderr or "").strip()[:4000],
    }
