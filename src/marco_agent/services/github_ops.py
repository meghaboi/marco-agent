from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marco_agent.services.secrets_provider import SecretProvider


@dataclass(slots=True)
class GitHubAuthProvider:
    secret_provider: SecretProvider

    def get_user_token(self, *, user_id: str) -> str | None:
        return self.secret_provider.get_secret(key=f"MARCO-GITHUB-TOKEN-{user_id}")

    def set_user_token(self, *, user_id: str, token: str) -> None:
        self.secret_provider.set_secret(key=f"MARCO-GITHUB-TOKEN-{user_id}", value=token.strip())


class GitHubWorkflowService:
    def __init__(self, *, auth_provider: GitHubAuthProvider, clone_base_dir: str) -> None:
        self._auth_provider = auth_provider
        self._clone_base_dir = Path(clone_base_dir)
        self._clone_base_dir.mkdir(parents=True, exist_ok=True)

    def clone_repo(self, *, user_id: str, repo_url: str, repo_alias: str | None = None) -> dict[str, Any]:
        token = self._auth_provider.get_user_token(user_id=user_id)
        if not token:
            return {"ok": False, "error": "Missing GitHub token for this user."}
        clone_url = _inject_token(repo_url=repo_url, token=token)
        repo_name = repo_alias or _repo_name_from_url(repo_url)
        target = self._clone_base_dir / f"{user_id}-{repo_name}"
        if target.exists():
            return {"ok": True, "repo_dir": str(target), "note": "already cloned"}
        result = _run_git(["git", "clone", clone_url, str(target)], cwd=self._clone_base_dir)
        return {"ok": result["ok"], "repo_dir": str(target), "stdout": result["stdout"], "stderr": result["stderr"]}

    def branch_commit_push(
        self,
        *,
        user_id: str,
        repo_dir: str,
        branch: str,
        commit_message: str,
    ) -> dict[str, Any]:
        token = self._auth_provider.get_user_token(user_id=user_id)
        if not token:
            return {"ok": False, "error": "Missing GitHub token for this user."}
        repo_path = Path(repo_dir)
        if not repo_path.exists():
            return {"ok": False, "error": f"Repo dir not found: {repo_dir}"}
        _run_git(["git", "checkout", "-B", branch], cwd=repo_path)
        _run_git(["git", "add", "-A"], cwd=repo_path)
        commit = _run_git(["git", "commit", "-m", commit_message], cwd=repo_path)
        push = _run_git(["git", "push", "-u", "origin", branch], cwd=repo_path)
        return {
            "ok": push["ok"],
            "branch": branch,
            "commit_stdout": commit["stdout"],
            "push_stdout": push["stdout"],
            "push_stderr": push["stderr"],
        }


def build_pr_checklist(*, template: str = "standard") -> str:
    if template == "infra":
        rows = [
            "- [ ] IaC plan reviewed",
            "- [ ] Security impact reviewed",
            "- [ ] Rollback steps documented",
        ]
    else:
        rows = [
            "- [ ] Unit tests added/updated",
            "- [ ] Manual test plan executed",
            "- [ ] Observability/logging impact reviewed",
            "- [ ] Backward compatibility checked",
        ]
    return "\n".join(rows)


def build_pr_body(*, summary: str, test_plan: str, risks: str, template: str = "standard") -> str:
    return (
        "## Summary\n"
        f"{summary.strip()}\n\n"
        "## Test Plan\n"
        f"{test_plan.strip()}\n\n"
        "## Risks\n"
        f"{risks.strip()}\n\n"
        "## Checklist\n"
        f"{build_pr_checklist(template=template)}"
    )


def _inject_token(*, repo_url: str, token: str) -> str:
    if repo_url.startswith("https://"):
        return repo_url.replace("https://", f"https://x-access-token:{token}@", 1)
    return repo_url


def _repo_name_from_url(repo_url: str) -> str:
    text = repo_url.rstrip("/")
    if text.endswith(".git"):
        text = text[: -len(".git")]
    return text.rsplit("/", maxsplit=1)[-1] or "repo"


def _run_git(cmd: list[str], *, cwd: Path) -> dict[str, Any]:
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "ok": result.returncode == 0,
        "code": result.returncode,
        "stdout": (result.stdout or "").strip(),
        "stderr": (result.stderr or "").strip(),
    }
