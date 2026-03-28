from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from marco_agent.config import DEFAULT_CONFIG_PATH, load_file_config
from marco_agent.tools.ops_tools import ops_tool_definitions
from marco_agent.tools.rag_tools import rag_tool_definitions
from marco_agent.tools.task_tools import task_tool_definitions


def _check_health(base_url: str) -> tuple[bool, str]:
    url = f"{base_url.rstrip('/')}/healthz"
    try:
        with urlopen(url, timeout=5) as response:
            body = response.read().decode("utf-8", errors="ignore")
    except URLError as exc:
        return False, f"health check failed: {exc}"
    return ('"status": "ok"' in body or '"status":"ok"' in body), body


async def _run(args: argparse.Namespace) -> int:
    cfg = load_file_config(Path(args.config))
    checks: list[tuple[str, bool, str]] = []
    checks.append(("config-load", True, f"chat={cfg.active_models.chat}"))
    checks.append(("task-tools", len(task_tool_definitions()) >= 4, "task defs loaded"))
    checks.append(("rag-tools", len(rag_tool_definitions()) >= 5, "rag defs loaded"))
    checks.append(("ops-tools", len(ops_tool_definitions()) >= 8, "ops defs loaded"))

    if args.health_url:
        ok, note = _check_health(args.health_url)
        checks.append(("healthz", ok, note[:200]))

    failed = [row for row in checks if not row[1]]
    print(json.dumps([{"name": name, "ok": ok, "note": note} for name, ok, note in checks], indent=2))
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Marco e2e smoke checks")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--health-url", default="")
    args = parser.parse_args()
    code = asyncio.run(_run(args))
    sys.exit(code)


if __name__ == "__main__":
    main()
