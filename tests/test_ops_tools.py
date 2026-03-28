import asyncio

from marco_agent.tools.ops_tools import OPS_TOOL_NAMES, execute_ops_tool_call, ops_tool_definitions


class StubGithubAuth:
    def __init__(self) -> None:
        self.token = ""

    def set_user_token(self, *, user_id, token):
        _ = user_id
        self.token = token


class StubGithubWorkflow:
    def clone_repo(self, *, user_id, repo_url, repo_alias=None):
        _ = (user_id, repo_alias)
        return {"ok": True, "repo_dir": "/tmp/repo", "repo_url": repo_url}

    def branch_commit_push(self, *, user_id, repo_dir, branch, commit_message):
        _ = (user_id, repo_dir, commit_message)
        return {"ok": True, "branch": branch}


class StubCodexAuth:
    def start_interactive_login(self, *, user_id):
        _ = user_id
        return {"ok": "true", "verification_code": "abc", "expires_at": "2026-03-28T00:00:00+00:00"}

    def complete_interactive_login(self, *, user_id, verification_code, token=None):
        _ = user_id
        if verification_code == "abc":
            return {"ok": "true"}
        return {"ok": "false", "error": "bad"}


class StubExecutionRunner:
    def run(self, *, mode, image, command, env=None):
        _ = (image, command, env)
        if mode in {"aca_job", "aci"}:
            return {"ok": "true", "runner": mode}
        return {"ok": "false"}


class StubNgrok:
    def open_tunnel(self, *, local_port, ttl_minutes=120):
        _ = (local_port, ttl_minutes)
        return {"ok": "true", "public_url": "https://x.ngrok.app"}

    def close_tunnel(self):
        return {"ok": "true"}

    def get_status(self):
        return {"ok": "true", "active": "false"}


def test_ops_tool_definitions_include_expected_tools() -> None:
    names = {item["function"]["name"] for item in ops_tool_definitions()}
    assert names == OPS_TOOL_NAMES


def test_execute_ops_tools_core_paths() -> None:
    auth = StubGithubAuth()
    workflow = StubGithubWorkflow()
    codex = StubCodexAuth()
    runner = StubExecutionRunner()
    ngrok = StubNgrok()

    token_set = asyncio.run(
        execute_ops_tool_call(
            user_id="u1",
            tool_name="github_token_set",
            arguments_json='{"token":"ghp_123"}',
            github_auth=auth,  # type: ignore[arg-type]
            github_workflow=workflow,  # type: ignore[arg-type]
            codex_auth=codex,  # type: ignore[arg-type]
            execution_runner=runner,  # type: ignore[arg-type]
            ngrok=ngrok,  # type: ignore[arg-type]
            pr_template="standard",
        )
    )
    assert token_set["ok"] is True

    clone = asyncio.run(
        execute_ops_tool_call(
            user_id="u1",
            tool_name="github_clone_repo",
            arguments_json='{"repo_url":"https://github.com/acme/repo.git"}',
            github_auth=auth,  # type: ignore[arg-type]
            github_workflow=workflow,  # type: ignore[arg-type]
            codex_auth=codex,  # type: ignore[arg-type]
            execution_runner=runner,  # type: ignore[arg-type]
            ngrok=ngrok,  # type: ignore[arg-type]
            pr_template="standard",
        )
    )
    assert clone["ok"] is True

    pr = asyncio.run(
        execute_ops_tool_call(
            user_id="u1",
            tool_name="github_generate_pr",
            arguments_json='{"summary":"s","test_plan":"t","risks":"r"}',
            github_auth=auth,  # type: ignore[arg-type]
            github_workflow=workflow,  # type: ignore[arg-type]
            codex_auth=codex,  # type: ignore[arg-type]
            execution_runner=runner,  # type: ignore[arg-type]
            ngrok=ngrok,  # type: ignore[arg-type]
            pr_template="standard",
        )
    )
    assert pr["ok"] is True
    assert "Checklist" in pr["pr_body"]

    codex_begin = asyncio.run(
        execute_ops_tool_call(
            user_id="u1",
            tool_name="codex_auth_begin",
            arguments_json="{}",
            github_auth=auth,  # type: ignore[arg-type]
            github_workflow=workflow,  # type: ignore[arg-type]
            codex_auth=codex,  # type: ignore[arg-type]
            execution_runner=runner,  # type: ignore[arg-type]
            ngrok=ngrok,  # type: ignore[arg-type]
            pr_template="standard",
        )
    )
    assert codex_begin["ok"] is True
    codex_complete = asyncio.run(
        execute_ops_tool_call(
            user_id="u1",
            tool_name="codex_auth_complete",
            arguments_json='{"verification_code":"abc"}',
            github_auth=auth,  # type: ignore[arg-type]
            github_workflow=workflow,  # type: ignore[arg-type]
            codex_auth=codex,  # type: ignore[arg-type]
            execution_runner=runner,  # type: ignore[arg-type]
            ngrok=ngrok,  # type: ignore[arg-type]
            pr_template="standard",
        )
    )
    assert codex_complete["ok"] is True

    tunnel = asyncio.run(
        execute_ops_tool_call(
            user_id="u1",
            tool_name="ngrok_open_tunnel",
            arguments_json='{"local_port":8080}',
            github_auth=auth,  # type: ignore[arg-type]
            github_workflow=workflow,  # type: ignore[arg-type]
            codex_auth=codex,  # type: ignore[arg-type]
            execution_runner=runner,  # type: ignore[arg-type]
            ngrok=ngrok,  # type: ignore[arg-type]
            pr_template="standard",
        )
    )
    assert tunnel["ok"] is True
