from __future__ import annotations

import asyncio
import json
from typing import Any

from marco_agent.services.codex_execution import CodexAuthSessionManager, ExecutionJobRunner
from marco_agent.services.github_ops import GitHubAuthProvider, GitHubWorkflowService, build_pr_body
from marco_agent.services.ngrok_manager import NgrokTunnelManager

OPS_TOOL_NAMES = {
    "github_token_set",
    "github_clone_repo",
    "github_branch_commit_push",
    "github_generate_pr",
    "codex_auth_begin",
    "codex_auth_complete",
    "execution_run_job",
    "ngrok_open_tunnel",
    "ngrok_close_tunnel",
    "ngrok_status",
}


def ops_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "github_token_set",
                "description": "Persist GitHub token in Key Vault-backed secrets for this user.",
                "parameters": {
                    "type": "object",
                    "properties": {"token": {"type": "string"}},
                    "required": ["token"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "github_clone_repo",
                "description": "Clone a GitHub repository using stored auth token.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo_url": {"type": "string"},
                        "repo_alias": {"type": "string"},
                    },
                    "required": ["repo_url"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "github_branch_commit_push",
                "description": "Create/switch branch, commit staged changes, and push.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo_dir": {"type": "string"},
                        "branch": {"type": "string"},
                        "commit_message": {"type": "string"},
                    },
                    "required": ["repo_dir", "branch", "commit_message"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "github_generate_pr",
                "description": "Generate PR body from summary/test plan/risks using checklist template.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "test_plan": {"type": "string"},
                        "risks": {"type": "string"},
                        "template": {"type": "string"},
                    },
                    "required": ["summary", "test_plan", "risks"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "codex_auth_begin",
                "description": "Start one-time interactive Codex auth flow.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "codex_auth_complete",
                "description": "Finish Codex auth flow using verification_code only.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "verification_code": {"type": "string"},
                    },
                    "required": ["verification_code"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "execution_run_job",
                "description": "Prepare execution command for ACA Job or ACI.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string", "enum": ["aca_job", "aci"]},
                        "image": {"type": "string"},
                        "command": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["mode", "image", "command"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ngrok_open_tunnel",
                "description": "Open ngrok tunnel with max 2-hour TTL policy.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "local_port": {"type": "integer"},
                        "ttl_minutes": {"type": "integer", "minimum": 1, "maximum": 120},
                    },
                    "required": ["local_port"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ngrok_close_tunnel",
                "description": "Close active ngrok tunnel session.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ngrok_status",
                "description": "Get active ngrok tunnel status and TTL.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
    ]


async def execute_ops_tool_call(
    *,
    user_id: str,
    tool_name: str,
    arguments_json: str,
    github_auth: GitHubAuthProvider,
    github_workflow: GitHubWorkflowService,
    codex_auth: CodexAuthSessionManager,
    execution_runner: ExecutionJobRunner,
    ngrok: NgrokTunnelManager,
    pr_template: str,
) -> dict[str, Any]:
    args = _load_tool_args(arguments_json)
    if tool_name not in OPS_TOOL_NAMES:
        return {"ok": False, "error": f"Unknown ops tool '{tool_name}'."}
    try:
        if tool_name == "github_token_set":
            token = str(args.get("token", "")).strip()
            if not token:
                return {"ok": False, "error": "token is required."}
            github_auth.set_user_token(user_id=user_id, token=token)
            return {"ok": True}
        if tool_name == "github_clone_repo":
            repo_url = str(args.get("repo_url", "")).strip()
            if not repo_url:
                return {"ok": False, "error": "repo_url is required."}
            result = await asyncio.to_thread(
                github_workflow.clone_repo,
                user_id=user_id,
                repo_url=repo_url,
                repo_alias=_as_optional_str(args.get("repo_alias")),
            )
            return {"ok": bool(result.get("ok")), **result}
        if tool_name == "github_branch_commit_push":
            result = await asyncio.to_thread(
                github_workflow.branch_commit_push,
                user_id=user_id,
                repo_dir=str(args.get("repo_dir", "")).strip(),
                branch=str(args.get("branch", "")).strip(),
                commit_message=str(args.get("commit_message", "")).strip(),
            )
            return {"ok": bool(result.get("ok")), **result}
        if tool_name == "github_generate_pr":
            body = build_pr_body(
                summary=str(args.get("summary", "")).strip(),
                test_plan=str(args.get("test_plan", "")).strip(),
                risks=str(args.get("risks", "")).strip(),
                template=_as_optional_str(args.get("template")) or pr_template,
            )
            return {"ok": True, "pr_body": body}
        if tool_name == "codex_auth_begin":
            row = codex_auth.start_interactive_login(user_id=user_id)
            return {**row, "ok": row.get("ok") == "true"}
        if tool_name == "codex_auth_complete":
            row = codex_auth.complete_interactive_login(
                user_id=user_id,
                verification_code=str(args.get("verification_code", "")).strip(),
                token=_as_optional_str(args.get("token")),
            )
            return {**row, "ok": row.get("ok") == "true"}
        if tool_name == "execution_run_job":
            row = execution_runner.run(
                mode=str(args.get("mode", "")).strip(),
                image=str(args.get("image", "")).strip(),
                command=[str(item) for item in args.get("command", []) if str(item).strip()],
            )
            return {**row, "ok": row.get("ok") == "true"}
        if tool_name == "ngrok_open_tunnel":
            row = ngrok.open_tunnel(
                local_port=int(args.get("local_port", 0)),
                ttl_minutes=max(1, min(int(args.get("ttl_minutes", 120)), 120)),
            )
            return {**row, "ok": row.get("ok") == "true"}
        if tool_name == "ngrok_close_tunnel":
            row = ngrok.close_tunnel()
            return {**row, "ok": row.get("ok") == "true"}
        if tool_name == "ngrok_status":
            row = ngrok.get_status()
            return {**row, "ok": row.get("ok") == "true"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": f"Unhandled ops tool '{tool_name}'."}


def _load_tool_args(arguments_json: str) -> dict[str, Any]:
    raw = (arguments_json or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
