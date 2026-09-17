"""平台沙箱后台任务编译与运行器测试。"""

import asyncio
from pathlib import Path
import pytest

from synlys_harness import ExecutionRequest, ToolResult

from app.runtime.assembly import prepare_skills
from app.services.job_access import JobSubmissionScope, prepare_sandbox_job
from app.services.sandbox_job_runner import SandboxJobRunner
from app.services.skill_service import ResolvedSkill


class _Settings:
    """后台任务编译使用的最小配置。"""

    sandbox_job_default_timeout_s = 1800.0
    sandbox_job_max_timeout_s = 7200.0


def _scope(tmp_path, *, tools=("python.run", "shell.run")):
    skill_dir = tmp_path / "skill"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "run.py").write_text("print('ok')", encoding="utf-8")
    skill = ResolvedSkill(
        name="demo", description="demo", body="body", directory=skill_dir,
        source="catalog",
    )
    prepared = prepare_skills([skill], sandbox="docker")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return JobSubmissionScope(
        allowed_tools=frozenset(tools),
        allowed_plugins=frozenset(),
        skills=prepared.items,
        resources=prepared.resources,
        workspace_root=workspace,
        ownership={"session_id": "s1"},
    )


def test_prepare_python_job_without_plugin(tmp_path):
    """授权 Python 的本轮即使没有插件也可编译平台任务。"""
    request = prepare_sandbox_job(
        "sandbox.python", {"code": "print(1)"}, _scope(tmp_path), _Settings(),
    )

    assert request.argv[-1] == "print(1)"
    assert request.workspace_root == (tmp_path / "workspace").resolve()
    assert request.timeout_s == 1800


def test_prepare_skill_job_uses_bound_directory_and_argv(tmp_path):
    """技能脚本来自提交时解析目录，并以 argv 传递参数。"""
    scope = _scope(tmp_path)
    request = prepare_sandbox_job(
        "sandbox.skill",
        {"skill": "demo", "script": "scripts/run.py", "args": ["--x", "1"]},
        scope,
        _Settings(),
    )

    assert request.argv == (
        "python", "-I", "-X", "utf8", "/skills/demo/scripts/run.py", "--x", "1",
    )
    assert request.resources == scope.resources


@pytest.mark.parametrize("kind,params,tools", [
    ("sandbox.python", {"code": "print(1)"}, ()),
    ("sandbox.shell", {"command": "echo ok"}, ("python.run",)),
    ("sandbox.skill", {"skill": "demo", "script": "scripts/run.py"}, ()),
    ("sandbox.skill", {"skill": "demo", "script": "../escape.py"}, ("python.run",)),
])
def test_prepare_job_rejects_permission_and_path_bypass(tmp_path, kind, params, tools):
    """后台入口不能绕过工具权限或技能脚本目录。"""
    with pytest.raises(ValueError):
        prepare_sandbox_job(kind, params, _scope(tmp_path, tools=tools), _Settings())


async def test_runner_start_returns_before_executor_finishes(tmp_path):
    """start 同步交接并持有句柄，不等待执行完成。"""
    gate = asyncio.Event()
    finished: list[tuple[str, ToolResult, bool]] = []

    class Executor:
        sandbox = "docker"

        async def execute(self, _request):
            await gate.wait()
            return ToolResult(ok=True, content="done", data={"exit_code": 0})

        async def cleanup_execution(self, _execution_id):
            return True

    async def running(_job_id):
        return None

    async def finish(job_id, result, *, cancelled=False):
        finished.append((job_id, result, cancelled))

    runner = SandboxJobRunner(Executor(), running, finish)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    request = ExecutionRequest(
        argv=("python", "-V"),
        workspace_root=workspace,
        execution_id="job-1",
    )

    runner.start("job-1", request)
    await asyncio.sleep(0)

    assert runner.is_running("job-1")
    assert finished == []
    gate.set()
    await runner.wait("job-1")
    assert finished[0][0] == "job-1"
    assert finished[0][1].content == "done"


async def test_runner_cancel_waits_for_confirmed_cleanup(tmp_path):
    """取消只有在执行器确认清理后才返回 True。"""
    started = asyncio.Event()
    cleaned: list[str] = []

    class Executor:
        sandbox = "docker"

        async def execute(self, _request):
            started.set()
            await asyncio.Event().wait()

        async def cleanup_execution(self, execution_id):
            cleaned.append(execution_id)
            return True

    async def noop(*_args, **_kwargs):
        return None

    runner = SandboxJobRunner(Executor(), noop, noop)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runner.start("job-2", ExecutionRequest(
        argv=("python", "-V"),
        workspace_root=workspace,
        execution_id="job-2",
    ))
    await started.wait()

    assert await runner.cancel("job-2") is True
    assert cleaned == ["job-2"]


async def test_recover_marks_only_sandbox_jobs_interrupted(tmp_path):
    """重启恢复只处理平台任务，外部任务保持原状。"""
    from app.db.repos import JobRepo
    from app.db.store import SqliteStore
    from app.plugins.contracts import JobConnectorRegistry
    from app.services.job_service import JobService

    store = SqliteStore(":memory:")
    await store.init()
    repo = JobRepo(store)
    sandbox = await repo.create({
        "backend": "sandbox", "kind": "sandbox.python", "status": "running",
        "session_id": "s1", "user_id": "u1",
    })
    external = await repo.create({
        "backend": "external", "kind": "spec.task.nmr", "status": "running",
        "session_id": "s1", "user_id": "u1", "external_id": "e1",
    })

    class Executor:
        sandbox = "docker"

        async def cleanup_execution(self, _execution_id):
            return True

    service = JobService(repo, JobConnectorRegistry())
    await service.recover_sandbox_jobs(Executor())

    sandbox_doc = await repo.get(sandbox["_id"])
    external_doc = await repo.get(external["_id"])
    assert sandbox_doc["status"] == "failed"
    assert sandbox_doc["error_code"] == "process_interrupted"
    assert external_doc["status"] == "running"
    await store.close()


async def test_real_docker_runs_three_sandbox_kinds_and_cancels(tmp_path):
    """真实 Docker 跑通三种平台任务，并确认取消清理容器。"""
    from synlys_harness import DockerCodeExecutor

    from app.db.repos import JobRepo
    from app.db.store import SqliteStore
    from app.plugins.contracts import JobConnectorRegistry
    from app.services.job_service import JobService

    executor = DockerCodeExecutor(
        image="synlora-sandbox:latest",
        deployment_id="pytest-sandbox-jobs",
    )
    docker_ok, reason = executor.probe()
    if not docker_ok:
        pytest.skip(f"Docker 沙箱不可用: {reason}")

    store = SqliteStore(":memory:")
    await store.init()
    repo = JobRepo(store)
    settings = _Settings()
    service = JobService(
        repo, JobConnectorRegistry(), settings=settings,
    )
    runner = SandboxJobRunner(
        executor, service.mark_sandbox_running, service.finish_sandbox,
    )
    service.set_sandbox_runner(runner)
    scope = _scope(tmp_path)

    async def submit(kind: str, params: dict) -> dict:
        result = await service.handle(
            {"action": "submit", "kind": kind, "params": params},
            user={"sub": "u1"},
            session_id="s1",
            ctx_extra={"job_submission_scope": scope},
        )
        assert result.ok, result.content
        await runner.wait(result.data["job_id"])
        return await service.get(result.data["job_id"])

    python_doc = await submit("sandbox.python", {"code": "print('python-ok')"})
    shell_doc = await submit("sandbox.shell", {"command": "echo shell-ok"})
    skill_doc = await submit("sandbox.skill", {
        "skill": "demo", "script": "scripts/run.py", "args": [],
    })

    assert python_doc["status"] == "completed" and "python-ok" in python_doc["result"]
    assert shell_doc["status"] == "completed" and "shell-ok" in shell_doc["result"]
    assert skill_doc["status"] == "completed" and "ok" in skill_doc["result"]

    repo_root = Path(__file__).resolve().parents[4]
    product_skill_dir = (
        repo_root / "apps" / "web" / "backend" / "catalog" / "skills"
        / "data-analysis"
    )
    (scope.workspace_root / "files").mkdir()
    (scope.workspace_root / "output").mkdir()
    (scope.workspace_root / "files" / "data.csv").write_text(
        "name,value\na,1\nb,2\n", encoding="utf-8"
    )
    product_skill = ResolvedSkill(
        name="data-analysis",
        description="CSV 摘要",
        body="正文",
        directory=product_skill_dir,
        source="catalog",
    )
    product_prepared = prepare_skills([product_skill], sandbox="docker")
    product_scope = JobSubmissionScope(
        allowed_tools=scope.allowed_tools,
        allowed_plugins=frozenset(),
        skills=product_prepared.items,
        resources=product_prepared.resources,
        workspace_root=scope.workspace_root,
        ownership=scope.ownership,
    )
    product_result = await service.handle(
        {
            "action": "submit",
            "kind": "sandbox.skill",
            "params": {
                "skill": "data-analysis",
                "script": "scripts/summarize_csv.py",
                "args": [
                    "--input", "/workspace/files/data.csv",
                    "--output", "/workspace/output/background-summary.json",
                ],
            },
        },
        user={"sub": "u1"},
        session_id="s1",
        ctx_extra={"job_submission_scope": product_scope},
    )
    assert product_result.ok, product_result.content
    await runner.wait(product_result.data["job_id"])
    product_doc = await service.get(product_result.data["job_id"])
    assert product_doc["status"] == "completed"
    product_output = (
        scope.workspace_root / "output" / "background-summary.json"
    ).read_text(encoding="utf-8")
    assert '"row_count": 2' in product_output

    long_result = await service.handle(
        {"action": "submit", "kind": "sandbox.python",
         "params": {"code": "import time; time.sleep(300)"}},
        user={"sub": "u1"}, session_id="s1",
        ctx_extra={"job_submission_scope": scope},
    )
    assert long_result.ok
    job_id = long_result.data["job_id"]
    for _ in range(100):
        doc = await service.get(job_id)
        if doc["status"] == "running":
            break
        await asyncio.sleep(0.05)
    cancelled = await service.cancel(job_id, user_id="u1")
    assert cancelled.ok
    assert (await service.get(job_id))["status"] == "cancelled"
    assert await executor.cleanup_execution(job_id)

    await runner.shutdown()
    await store.close()
