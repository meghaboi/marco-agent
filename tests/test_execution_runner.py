from marco_agent.services.codex_execution import ExecutionJobRunner


def test_execution_runner_aca_dry_run() -> None:
    runner = ExecutionJobRunner(
        aca_job_name="job1",
        aca_resource_group="rg1",
        aci_resource_group="",
        execute_commands=False,
    )
    result = runner.run(mode="aca_job", image="x", command=["echo", "hi"])
    assert result["ok"] == "true"
    assert result["dry_run"] == "true"
    assert "containerapp job start" in result["command"]


def test_execution_runner_aci_dry_run() -> None:
    runner = ExecutionJobRunner(
        aca_job_name="",
        aca_resource_group="",
        aci_resource_group="rg1",
        execute_commands=False,
    )
    result = runner.run(mode="aci", image="repo/image:latest", command=["python", "-V"])
    assert result["ok"] == "true"
    assert result["dry_run"] == "true"
    assert "container create" in result["command"]
