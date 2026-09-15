# 用户级内容后端（数据目录分层 + 安装模型 + 用户技能/专家）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把数据目录改为按用户分层（`users/<uid>/{workspaces,sessions,skills,experts}` + `public/{skills,catalog}`），内置内容改为"安装 + 启用"后才可用，并让用户能自建技能与专家。

**Architecture:** 沿用"内置内容单一来源 + 安装记录"的既有机制：`catalog/` 只读不动，`user_capabilities` 增加 `enabled` 字段表达启用态，`catalog_policy` 缺省从"默认启用"改为"需安装"。用户自建内容落各自的 `users/<uid>/` 目录；用户专家以文件为事实源、读时幂等实例化进 `assistants` 集合，从而不改会话绑定与助手管理。

**Tech Stack:** Python 3.12 / FastAPI / pytest（conda 环境 `synlysagent`）；存储双后端 sqlite/mongodb（`DocumentStore`）。

**规格来源:** `docs/superpowers/specs/2026-09-15-synlysagent-user-content-and-data-layout-design.md`

**测试命令约定:** 所有 pytest 命令在 `apps/web/backend` 目录下执行：
`cd apps/web/backend && conda run -n synlysagent python -m pytest <路径> -v`

---

## Phase 1 — 数据目录分层

### Task 1: workspace.py 路径根改到 users/<uid>，删除旧布局迁移

**Files:**
- Modify: `apps/web/backend/app/services/workspace.py`
- Test: `apps/web/backend/tests/test_workspace_layout.py`

- [ ] **Step 1: 改测试（先写期望的新路径）**

在 `tests/test_workspace_layout.py` 中：删除 4 个旧迁移用例
（`test_legacy_migration_moves_subdirs`、`test_legacy_migration_is_idempotent`、
`test_migrate_legacy_moves_partial_subdirs`、`test_migrate_legacy_resumes_after_partial`），
并把 `test_project_root_creates_subdirs` 改为断言新路径，新增三条路径用例：

```python
def test_user_root_under_users_dir(tmp_path: Path):
    assert workspace.user_root(tmp_path, "u1") == tmp_path / "users" / "u1"


def test_project_root_under_users_workspaces(tmp_path: Path):
    root = workspace.project_root(tmp_path, "u1", "default")
    assert root == tmp_path / "users" / "u1" / "workspaces" / "default"
    assert (root / "files").is_dir() and (root / "output").is_dir() and (root / "tmp").is_dir()


def test_public_and_user_skill_roots(tmp_path: Path):
    assert workspace.public_skills_root(tmp_path) == tmp_path / "public" / "skills"
    assert workspace.user_skills_root(tmp_path, "u1") == tmp_path / "users" / "u1" / "skills"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `conda run -n synlysagent python -m pytest tests/test_workspace_layout.py -v`
Expected: FAIL —— `user_root` 仍返回 `tmp_path/"workspaces"/"u1"`，且 `workspace.public_skills_root` 不存在（AttributeError）。

- [ ] **Step 3: 实现**

在 `app/services/workspace.py` 中：

1) `user_root` 返回值改为：

```python
    return data_root / "users" / user_id
```

2) `project_root` 中间那行改为：

```python
    root = data_root / "users" / user_id / "workspaces" / dir_name
```

3) 删除函数 `workspace_root`（废弃兼容用）与 `migrate_legacy_layout`（不再兼容旧布局），
并同步删掉模块顶部不再使用的 `import os`。

4) 新增两个路径函数（放在 `project_root` 之后）：

```python
def public_skills_root(data_root: Path) -> Path:
    """公共可写技能层根（管理员自建/导入）。

    Args:
        data_root: 数据根目录。

    Returns:
        {data_root}/public/skills 路径（可能不存在）。
    """
    return data_root / "public" / "skills"


def user_skills_root(data_root: Path, user_id: str) -> Path:
    """用户自建技能根。

    Args:
        data_root: 数据根目录。
        user_id: 用户 sub。

    Returns:
        {data_root}/users/{user_id}/skills 路径（可能不存在）。
    """
    return data_root / "users" / user_id / "skills"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `conda run -n synlysagent python -m pytest tests/test_workspace_layout.py -v`
Expected: PASS（除已删除的迁移用例外全绿）。

- [ ] **Step 5: 提交**

```bash
git add apps/web/backend/app/services/workspace.py apps/web/backend/tests/test_workspace_layout.py
git commit -m "refactor: 工作区路径根改到 users/<uid>，删除旧布局迁移"
```

---

### Task 2: ProjectService 适配新路径，并钉死默认工作区目录名

**Files:**
- Modify: `apps/web/backend/app/services/project_service.py`
- Test: `apps/web/backend/tests/test_project_service.py`

**背景：** 现状 `_create_locked` 用 `free_dir_name` 求目录名，默认项目也会被"占用避让"成
`default-2`（磁盘上有 `default` 孤儿目录、或 DB 与磁盘漂移时就会发生）。改为：用户自建工作区
**永不允许**占用 `default` 这个名字；默认项目的目录名**恒为** `default`。

- [ ] **Step 1: 改测试**

**1) 先清理"按旧布局铺数据"的既有测试**（T1 删掉迁移逻辑后它们必然失败，本任务一并认领；
Task 15 的全绿门槛依赖这一步）：

- `tests/test_project_service.py:12-17`、`:154`：路径 `data_root/"workspaces"/...` → `data_root/"users"/<uid>/"workspaces"/...`
- `tests/test_chat_api.py:934`、`:1159`：同上
- `tests/test_files_api.py:336`、`:383`、`:438`：这三条用例铺 `{data_root}/workspaces/u-user/files`
  并断言"旧文件被迁进活跃项目目录"。迁移已移除，改为断言**新布局下**的归属解析
  （文件直接放在 `users/u-user/workspaces/<项目>/files/`，断言按磁盘位置能解析到归属项目）；
  用例名与注释里的"migration/legacy"措辞同步改为当前语义。
- `app/services/project_service.py:285` 与 `app/api/files_api.py:3`、`app/core/settings.py:19`
  的注释里"workspaces/"路径说明同步改为 `users/<uid>/workspaces/`。

**2) 再改本文件的目标用例**（在 `tests/test_project_service.py` 中）：

删除 `test_list_ensures_default_project_on_legacy`（旧布局迁移已移除）。
2) 把 `test_concurrent_list_seeds_single_default_project` 改为"并发首条消息只建一个默认工作区"：

```python
async def test_concurrent_first_message_creates_single_default_project(tmp_path, store):
    service = ProjectService(store, tmp_path)
    results = await asyncio.gather(*[service.resolve_active_project("u1", None) for _ in range(5)])
    assert {r["_id"] for r in results} == {results[0]["_id"]}
    assert results[0]["dir_name"] == "default"
    assert (tmp_path / "users" / "u1" / "workspaces" / "default").is_dir()
```

3) 新增两条：

```python
async def test_user_project_cannot_take_default_dir_name(tmp_path, store):
    """用户自建工作区永不占用 default 这个名字，default 恒留给默认工作区。

    先建用户项目（此时还没有默认工作区，占用集合里一条记录都没有）：不把 default
    钉成已占用，用户项目就会直接拿走它，默认工作区只能退成 default-2。
    """
    service = ProjectService(store, tmp_path)
    project = await service.create_project("u1", "default")
    assert project["dir_name"] != "default"
    user_dir = tmp_path / "users" / "u1" / "workspaces"
    assert not (user_dir / "default").exists()  # default 没被用户项目吃掉
    # 该用户已无项目时创建默认工作区，仍拿得到 default（未被避让成 default-2）
    assert await service.delete_project("u1", project["_id"]) is True
    default_project = await service.resolve_active_project("u1", None)
    assert default_project["dir_name"] == "default"
    assert (user_dir / "default").is_dir()


async def test_rename_to_default_name_does_not_take_default_dir(tmp_path, store):
    """把用户项目改名为 default 时同样不能占用 default 目录（改名的 create 路径）。"""
    service = ProjectService(store, tmp_path)
    project = await service.create_project("u1", "exp")
    renamed = await service.rename_project("u1", project["_id"], "default")
    assert renamed["dir_name"] != "default"
    assert not (tmp_path / "users" / "u1" / "workspaces" / "default").exists()


async def test_rename_default_project_keeps_default_dir(tmp_path, store):
    """重命名默认工作区只改显示名：目录名恒为 default，否则会再长出第二个 default。"""
    service = ProjectService(store, tmp_path)
    project = await service.resolve_active_project("u1", None)
    renamed = await service.rename_project("u1", project["_id"], "我的常用")
    assert renamed["name"] == "我的常用" and renamed["dir_name"] == "default"
    assert (tmp_path / "users" / "u1" / "workspaces" / "default").is_dir()


async def test_default_project_reuses_orphan_default_dir(tmp_path, store):
    (tmp_path / "users" / "u1" / "workspaces" / "default").mkdir(parents=True)
    service = ProjectService(store, tmp_path)
    project = await service.resolve_active_project("u1", None)
    assert project["dir_name"] == "default"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `conda run -n synlysagent python -m pytest tests/test_project_service.py -v`
Expected: FAIL —— 路径断言落在 `workspaces/`（旧路径）；`test_user_project_cannot_take_default_dir_name`
会拿到 `dir_name == "default"`。

- [ ] **Step 3: 实现**

在 `app/services/project_service.py` 中：

1) 删除模块常量 `LEGACY_DEFAULT_PROJECT_NAME`。

2) `_list_locked` 整体替换为（去掉迁移与旧名订正）：

```python
    async def _list_locked(self, user_id: str) -> list[dict]:
        """锁内列出项目。

        调用方必须已持有该用户的锁（asyncio.Lock 不可重入，故不能直接调
        list_projects）。

        Args:
            user_id: 用户 sub。

        Returns:
            未归档项目文档列表（updated_at 倒序）。
        """
        return await self._repo.list_for_user(user_id)
```

3) `create_project` / `rename_project` 求目录名时把 `default` 视为已占用：

`create_project` 里改为：

```python
            taken = await self._repo.used_dir_names(user_id)
            taken.add(workspace.DEFAULT_PROJECT_DIR)
            dir_name = workspace.free_dir_name(
                self._user_workspaces_dir(user_id), base, taken)
```

`rename_project` 里同处改为：

```python
                taken = await self._repo.used_dir_names(user_id)
                taken.discard(old_dir)  # 自己不算占用，否则会被判成冲突而加后缀
                # default 恒留给默认工作区（无条件保留：改名成 default 时该落在 default-2）
                taken.add(workspace.DEFAULT_PROJECT_DIR)
                new_dir = workspace.free_dir_name(user_dir, base, taken)
```

4) 把 `_create_locked` 与新的默认项目方法改为：

```python
    def _user_workspaces_dir(self, user_id: str) -> Path:
        """该用户的工作区目录（{data_root}/users/{user_id}/workspaces）。

        Args:
            user_id: 用户 sub。

        Returns:
            工作区目录路径（可能不存在）。
        """
        return self._data_root / "users" / user_id / "workspaces"

    async def _create_locked(self, user_id: str, name: str, base: str) -> dict:
        """锁内新建项目（调用方必须已持有该用户的锁）。

        Args:
            user_id: 用户 sub。
            name: 项目显示名。
            base: sanitize 后的基础目录名。

        Returns:
            新建的项目文档。
        """
        user_dir = self._user_workspaces_dir(user_id)
        taken = await self._repo.used_dir_names(user_id)
        taken.add(workspace.DEFAULT_PROJECT_DIR)  # default 恒留给默认工作区
        dir_name = workspace.free_dir_name(user_dir, base, taken)
        project = await self._repo.create(
            user_id=user_id, name=name.strip(), dir_name=dir_name)
        workspace.project_root(self._data_root, user_id, dir_name)
        return project

    async def _ensure_default_locked(self, user_id: str) -> dict:
        """锁内确保该用户的默认工作区存在（目录名恒为 default，不参与避让）。

        磁盘上残留同名孤儿目录时直接复用（mkdir exist_ok），避免又造出 default-2。

        Args:
            user_id: 用户 sub。

        Returns:
            默认工作区文档。
        """
        for doc in await self._repo.list_for_user(user_id):
            if doc["dir_name"] == workspace.DEFAULT_PROJECT_DIR:
                return doc
        project = await self._repo.create(
            user_id=user_id, name=DEFAULT_PROJECT_NAME,
            dir_name=workspace.DEFAULT_PROJECT_DIR)
        workspace.project_root(self._data_root, user_id, workspace.DEFAULT_PROJECT_DIR)
        return project
```

5) `resolve_active_project` 回落分支改为：

```python
        async with self._lock_for(user_id):
            projects = await self._list_locked(user_id)
            if projects:
                return projects[0]
            return await self._ensure_default_locked(user_id)
```

6) `_create_locked` 之外还引用 `self._data_root / "workspaces" / user_id` 的地方
（`_create_locked` 与 `rename_project`）统一改用 `self._user_workspaces_dir(user_id)`。
`rename_project` 里的 `old_dir` 计算不变（`project["dir_name"]`）。

7) 重命名**默认工作区**时钉住目录名（只改显示名）。`rename_project` 的目录名分支改为：

```python
            if old_dir == workspace.DEFAULT_PROJECT_DIR:
                # 默认工作区的目录名恒为 default：只改显示名，否则会再长出第二个 default
                new_dir = old_dir
            elif base == old_dir:
                new_dir = old_dir
            else:
                taken = await self._repo.used_dir_names(user_id)
                taken.discard(old_dir)  # 自己不算占用，否则会被判成冲突而加后缀
                # default 恒留给默认工作区（无条件保留：改名成 default 时该落在 default-2）
                taken.add(workspace.DEFAULT_PROJECT_DIR)
                new_dir = workspace.free_dir_name(user_dir, base, taken)
```

并在 `rename_project` 的 docstring 补一句：**默认工作区只允许改显示名，磁盘目录名恒为 `default`**。

9) **大小写变体同样避让**（Windows/macOS 大小写不敏感盘上 `Default`/`DEFAULT` 与 `default` 是同一个物理目录）：
在 `_create_locked` 与 `rename_project` 两处 `taken.add(workspace.DEFAULT_PROJECT_DIR)` 之后各补一行：

```python
            if base.casefold() == workspace.DEFAULT_PROJECT_DIR:
                # 大小写变体（Default/DEFAULT）在大小写不敏感盘上与 default 同目录，必须避让
                taken.add(base)
```

并加测试：

```python
async def test_case_variant_of_default_does_not_take_default_dir(tmp_path, store):
    """Windows 大小写不敏感：Default/DEFAULT 与 default 同目录，同样必须避让。"""
    service = ProjectService(store, tmp_path)
    project = await service.create_project("u1", "Default")
    assert project["dir_name"].casefold() != "default"
```

8) 顺手清掉因本任务而失效的注释口径（只改注释，不动逻辑）：
`app/api/projects_api.py:21`「首次访问会迁移旧布局并补种默认项目」、
`app/api/sessions_api.py:378`「补种默认项目 / 迁移旧布局」、
`tests/test_projects_api.py:8`「迁移无副作用，不补种默认项目」、
`tests/test_e2e.py:56` 里的旧路径 —— 统一改为当前语义（不迁移、不补种）。
`apps/web/backend/README.md` 的 `workspaces/` 路径说明留给 Task 15 统一同步。

- [ ] **Step 4: 运行测试确认通过**

Run: `conda run -n synlysagent python -m pytest tests/test_project_service.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add apps/web/backend/app/services/project_service.py apps/web/backend/tests/test_project_service.py
git commit -m "refactor: 项目目录迁移到 users/<uid>/workspaces，默认工作区目录名恒为 default"
```

---

### Task 3: 会话事件落 users/<uid>/sessions/<sid>

**Files:**
- Modify: `apps/web/backend/app/services/agent_service.py:186-197`（`_jsonl_path`）、`:264`（调用点）
- Test: `apps/web/backend/tests/test_chat_api.py`

- [ ] **Step 1: 改测试（4 处路径断言）**

`tests/test_chat_api.py` 中 218、293、366、691 行的路径断言
（形如 `app.state.settings.data_root / "sessions" / sid / "events.jsonl"`）统一改为：

```python
    jsonl = (app.state.settings.data_root / "users" / USER_SUB
             / "sessions" / sid / "events.jsonl")
```

其中 `USER_SUB` 用该测试文件里已登录用户的 sub（若测试用 sqlite 开发 token，
sub 为 `"dev"`；以文件内既有登录方式为准，保持与请求头一致）。

- [ ] **Step 2: 运行测试确认失败**

Run: `conda run -n synlysagent python -m pytest tests/test_chat_api.py -v`
Expected: FAIL —— 事件文件仍落在 `data_root/sessions/<sid>/events.jsonl`，新断言的文件不存在。

- [ ] **Step 3: 实现**

`agent_service.py` 中 `_jsonl_path` 替换为：

```python
    def _jsonl_path(self, session_id: str, user_id: str) -> Path:
        """会话事件文件路径（父目录自动创建）。

        Args:
            session_id: 会话 id。
            user_id: 用户 sub（事件随会话归入该用户目录）。

        Returns:
            {data_root}/users/{user_id}/sessions/{session_id}/events.jsonl。
        """
        p = (self._settings.data_root / "users" / user_id
             / "sessions" / session_id / "events.jsonl")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
```

调用点（`chat()` 内，约 264 行）改为：

```python
                    with self._jsonl_path(
                            session_id, str(user.get("sub") or "anonymous")).open(
                                "a", encoding="utf-8") as f:
```

- [ ] **Step 4: 运行测试确认通过**

Run: `conda run -n synlysagent python -m pytest tests/test_chat_api.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add apps/web/backend/app/services/agent_service.py apps/web/backend/tests/test_chat_api.py
git commit -m "refactor: 会话事件落盘改到 users/<uid>/sessions/<sid>"
```

---

### Task 4: 公共层与 catalog 第二根改到 public/

**Files:**
- Modify: `apps/web/backend/app/catalog/loader.py:128-141`（`catalog_roots`）
- Modify: `apps/web/backend/app/services/skill_service.py:101-110`（`skills_dir`）
- Modify: `apps/web/backend/app/main.py:82-84`
- Test: `apps/web/backend/tests/test_catalog_loader.py`、`tests/test_skill_service.py`

- [ ] **Step 1: 改测试**

`tests/test_skill_service.py` 新增一条：

```python
def test_public_skills_dir_is_under_public(tmp_path):
    assert SkillService(tmp_path).skills_dir == tmp_path / "public" / "skills"
```

`tests/test_catalog_loader.py` 中若有断言第二根路径（形如 `data_root / "catalog"`）的用例，
改为 `data_root / "public" / "catalog"`；若只是断言"两个根、后者覆盖前者"，保持不动。

- [ ] **Step 2: 运行测试确认失败**

Run: `conda run -n synlysagent python -m pytest tests/test_skill_service.py::test_public_skills_dir_is_under_public -v`
Expected: FAIL —— 实际为 `tmp_path/skills`。

- [ ] **Step 3: 实现**

`catalog/loader.py` 的 `catalog_roots` 返回列表改为：

```python
    return [
        Path(__file__).resolve().parents[2] / "catalog",
        settings.data_root / "public" / "catalog",
    ]
```

并把该函数 docstring 里的 `{data_dir}/catalog/` 改为 `{data_dir}/public/catalog/`。

`skill_service.py` 的 `skills_dir` 属性改为：

```python
    @property
    def skills_dir(self) -> Path:
        """公共可写技能层根目录（自动创建）。

        Returns:
            {data_root}/public/skills 路径，不存在时已创建。
        """
        d = self._data_root / "public" / "skills"
        d.mkdir(parents=True, exist_ok=True)
        return d
```

`main.py` 中技能服务构造处（约 80-86 行）同步更新注释并保留逻辑：

```python
    # 内置技能：catalog/skills 作为只读根直接提供（与插件技能根同一模式），
    # {data_dir}/public/skills 为公共层（管理员自建/导入，始终可见）
    catalog_skills_root = catalog_roots(settings)[0] / "skills"
    app.state.skill_service = SkillService(settings.data_root, extra_roots=[catalog_skills_root])
    removed = app.state.skill_service.migrate_legacy_builtin_copies(catalog_skills_root)
    if removed:
        logger.info("已清理迁移前的内置技能旧副本: %s", removed)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `conda run -n synlysagent python -m pytest tests/test_skill_service.py tests/test_catalog_loader.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add apps/web/backend/app/catalog/loader.py apps/web/backend/app/services/skill_service.py apps/web/backend/app/main.py apps/web/backend/tests
git commit -m "refactor: 公共技能层与 catalog 第二根改到 data_root/public"
```

---

## Phase 2 — 安装与启用模型

### Task 5: user_capabilities 增加 enabled 字段与读写方法

**Files:**
- Modify: `apps/web/backend/app/catalog/user_caps.py`
- Test: `apps/web/backend/tests/test_catalog_user_caps.py`

- [ ] **Step 1: 写测试**

在 `tests/test_catalog_user_caps.py` 追加：

```python
async def test_install_records_enabled_true(store):
    repo = UserCapabilityRepo(store)
    await repo.install("u1", "skill", "demo")
    doc = await store.get(USER_CAPS_COLLECTION, "u1:skill:demo")
    assert doc["enabled"] is True


async def test_set_enabled_toggles(store):
    repo = UserCapabilityRepo(store)
    await repo.install("u1", "skill", "demo")
    assert await repo.set_enabled("u1", "skill", "demo", False) is True
    assert await repo.is_enabled("u1", "skill", "demo") is False
    assert await repo.set_enabled("u1", "skill", "demo", True) is True
    assert await repo.is_enabled("u1", "skill", "demo") is True


async def test_set_enabled_missing_record_returns_false(store):
    repo = UserCapabilityRepo(store)
    assert await repo.set_enabled("u1", "skill", "nope", False) is False


async def test_enabled_item_ids_only_lists_enabled(store):
    repo = UserCapabilityRepo(store)
    await repo.install("u1", "skill", "a")
    await repo.install("u1", "skill", "b")
    await repo.set_enabled("u1", "skill", "b", False)
    assert await repo.enabled_item_ids("u1", "skill") == {"a"}


async def test_enabled_defaults_true_for_legacy_docs(store):
    await store.insert(USER_CAPS_COLLECTION, {
        "_id": "u1:skill:legacy", "user_id": "u1", "kind": "skill",
        "item_id": "legacy", "installed_at": 0.0,
    })
    repo = UserCapabilityRepo(store)
    assert await repo.is_enabled("u1", "skill", "legacy") is True
```

在文件顶部 import 处补上 `USER_CAPS_COLLECTION`（若未导入）。

- [ ] **Step 2: 运行测试确认失败**

Run: `conda run -n synlysagent python -m pytest tests/test_catalog_user_caps.py -v`
Expected: FAIL —— `install` 写入的文档没有 `enabled` 字段（KeyError），`set_enabled`/`is_enabled`/`enabled_item_ids` 不存在。

- [ ] **Step 3: 实现**

`catalog/user_caps.py` 中：

1) `install` 的插入体加字段（docstring 补一句"enabled 缺省 True：装上即启用"）：

```python
            await self._store.insert(USER_CAPS_COLLECTION, {
                "_id": doc_id, "user_id": user_id, "kind": kind, "item_id": item_id,
                "enabled": True, "installed_at": time.time(),
            })
```

2) 新增三个方法（放在 `is_installed` 之后）：

```python
    async def set_enabled(self, user_id: str, kind: str, item_id: str,
                          enabled: bool) -> bool:
        """设置已安装条目的启用态。

        Args:
            user_id: 用户 sub。
            kind: 条目类型。
            item_id: 条目 id。
            enabled: True 启用 / False 停用。

        Returns:
            True 表示记录存在并已更新；未安装返回 False。
        """
        doc_id = cap_id(user_id, kind, item_id)
        if await self._store.get(USER_CAPS_COLLECTION, doc_id) is None:
            return False
        await self._store.update(USER_CAPS_COLLECTION, doc_id, {"enabled": bool(enabled)})
        return True

    async def is_enabled(self, user_id: str, kind: str, item_id: str) -> bool:
        """已安装条目是否处于启用态（未安装或脏文档按缺省 True 处理）。

        Args:
            user_id: 用户 sub。
            kind: 条目类型。
            item_id: 条目 id。

        Returns:
            True 表示启用；未安装返回 False。
        """
        doc = await self._store.get(USER_CAPS_COLLECTION, cap_id(user_id, kind, item_id))
        if doc is None:
            return False
        return bool(doc.get("enabled", True))

    async def enabled_item_ids(self, user_id: str, kind: str) -> set[str]:
        """某用户某类型下"已安装且启用"的条目 id 集合。

        Args:
            user_id: 用户 sub。
            kind: 条目类型。

        Returns:
            条目 id 集合。
        """
        docs = await self._store.list(USER_CAPS_COLLECTION, filters={"user_id": user_id})
        return {
            str(d.get("item_id"))
            for d in docs
            if d.get("kind") == kind and d.get("item_id") and bool(d.get("enabled", True))
        }
```

3) 模块 docstring 更新为：

```
存储形态（集合 user_capabilities，_id = f"{user_id}:{kind}:{item_id}"）：
    {"_id": "u1:plugin:spec_agent", "user_id": "u1", "kind": "plugin",
     "item_id": "spec_agent", "enabled": True, "installed_at": ...}
设计取舍（用户已确认）：内置包留在仓库，安装只写记录——升级即生效、无副本漂移；
enabled=false 表示"已装但停用"（运行期与未装同样不可见）；用户自建内容落
data_root/users/<uid>/（由 SkillService / UserExpertService 管理）。
```

- [ ] **Step 4: 运行测试确认通过**

Run: `conda run -n synlysagent python -m pytest tests/test_catalog_user_caps.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add apps/web/backend/app/catalog/user_caps.py apps/web/backend/tests/test_catalog_user_caps.py
git commit -m "feat: 安装记录增加启用态（已装但可停用）"
```

---

### Task 6: 策略缺省改为"需安装"

**Files:**
- Modify: `apps/web/backend/app/catalog/policy.py`
- Test: `apps/web/backend/tests/test_catalog_policy.py`

- [ ] **Step 1: 改测试**

把 `test_default_policy_is_public_enabled` 整个替换为：

```python
async def test_default_policy_is_public_but_not_enabled(store):
    repo = CatalogPolicyRepo(store)
    pol = await repo.get("skill", "demo")
    assert pol == {"visibility": "public", "default_enabled": False}
```

并在 `tests/test_catalog_service.py` 里检查是否有依赖"缺省即可见"的用例（搜索
`default_enabled=True` 或未安装即断言可见的断言），把这类断言改为"先装再断言可见"。

- [ ] **Step 2: 运行测试确认失败**

Run: `conda run -n synlysagent python -m pytest tests/test_catalog_policy.py -v`
Expected: FAIL —— 缺省仍是 `default_enabled=True`。

- [ ] **Step 3: 实现**

`catalog/policy.py`：

1) `get()` 的缺省分支改为：

```python
        if doc is None:
            return {"visibility": "public", "default_enabled": False}
```

2) `all_policies()` 里 `bool(d.get("default_enabled", True))` 改为
`bool(d.get("default_enabled", False))`。

3) 模块 docstring 第 7 行改为：

```
缺省（无记录）= public + default_enabled=False：条目在市场可见，但需用户安装后才可用。
```

- [ ] **Step 4: 运行测试确认通过**

Run: `conda run -n synlysagent python -m pytest tests/test_catalog_policy.py tests/test_catalog_service.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add apps/web/backend/app/catalog/policy.py apps/web/backend/tests
git commit -m "feat: 内置条目策略缺省改为需安装（public + 非默认启用）"
```

---

### Task 7: CapabilityService 把停用视为不可见，并在市场行暴露 enabled

**Files:**
- Modify: `apps/web/backend/app/catalog/service.py`
- Test: `apps/web/backend/tests/test_catalog_service.py`

- [ ] **Step 1: 写测试**

在 `tests/test_catalog_service.py` 追加（沿用该文件既有的 `caps` fixture 风格；若 fixture 只造 catalog，
需在 fixture 里传入 `UserCapabilityRepo`）：

```python
async def test_disabled_install_is_not_visible(caps, store):
    caps.installs = UserCapabilityRepo(store)
    await caps.installs.install("u1", "skill", "demo")
    assert "demo" in await caps.visible_ids("u1", "skill")
    await caps.installs.set_enabled("u1", "skill", "demo", False)
    assert "demo" not in await caps.visible_ids("u1", "skill")
    assert "demo" in await caps.hidden_skill_names("u1")


async def test_market_items_expose_enabled(caps, store):
    caps.installs = UserCapabilityRepo(store)
    await caps.installs.install("u1", "skill", "demo")
    await caps.installs.set_enabled("u1", "skill", "demo", False)
    rows = await caps.market_items("u1", "skill")
    row = next(r for r in rows if r["id"] == "demo")
    assert row["installed"] is True and row["enabled"] is False and row["visible"] is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `conda run -n synlysagent python -m pytest tests/test_catalog_service.py -v`
Expected: FAIL —— 停用后 `visible_ids` 仍包含 `demo`（当前只看安装记录），且市场行没有 `enabled` 键。

- [ ] **Step 3: 实现**

`catalog/service.py` 中：

1) `visible_ids` 改为按"已装且启用"判定：

```python
        # 一次取回该用户的启用集，避免逐条查询
        enabled = await self.installs.enabled_item_ids(user_id, kind)
        out: set[str] = set()
        for item in items:
            pol = await self.policy.get(kind, item.id)
            if pol["visibility"] == "hidden":
                continue
            if pol["default_enabled"] or item.id in enabled:
                out.add(item.id)
        return out
```

（删掉原先的 `installed = set(await self.installs.list_for_user(user_id, kind=kind))` 与
`f"{kind}:{item.id}" in installed` 分支。）

2) `market_items` 改为：

```python
        installed = set(await self.installs.list_for_user(user_id, kind=kind))
        enabled = await self.installs.enabled_item_ids(user_id, kind)
        rows: list[dict] = []
        for item in self.catalog.list_items(kind):
            pol = await self.policy.get(kind, item.id)
            hidden = pol["visibility"] == "hidden"
            if hidden and not admin:
                continue
            is_installed = f"{kind}:{item.id}" in installed
            is_enabled = item.id in enabled
            row = {
                "kind": item.kind, "id": item.id, "name": item.name,
                "description": item.description, "source": item.source,
                "visibility": pol["visibility"],
                "default_enabled": pol["default_enabled"],
                "installed": is_installed,
                "enabled": is_enabled,
                "visible": (not hidden) and (pol["default_enabled"] or is_enabled),
            }
            if kind == "plugin":
                pkg = self.catalog.plugins.get(item.id)
                row["config_schema"] = list(pkg.config_schema) if pkg is not None else []
            rows.append(row)
        return rows
```

3) `can_install` 不变（hidden 不可装）。

**插件侧无需额外改动：** `AgentService._visible_plugin_configs`（`agent_service.py:162`）与
`visible_tool_names` 都走 `visible_ids(user_id, "plugin")`，所以"只注入 installed+enabled 的插件"
（规格 §7）由本任务自动满足——停用的插件配置不再注入 `ctx.extra["plugins"]`，其工具也被过滤。

- [ ] **Step 4: 运行测试确认通过**

Run: `conda run -n synlysagent python -m pytest tests/test_catalog_service.py tests/test_capability_enforcement.py -v`
Expected: PASS（`test_capability_enforcement.py` 若因"缺省需安装"而失败，按新语义改为先安装）。

- [ ] **Step 5: 提交**

```bash
git add apps/web/backend/app/catalog/service.py apps/web/backend/tests
git commit -m "feat: 停用的已装能力按不可见处理，市场行暴露 enabled"
```

---

### Task 8: 能力开关端点 PUT /api/v1/me/capabilities/{kind}/{id}

**Files:**
- Modify: `apps/web/backend/app/catalog/api.py`
- Test: `apps/web/backend/tests/test_catalog_api.py`（新建）

- [ ] **Step 1: 写测试（新建文件）**

创建 `tests/test_catalog_api.py`：

```python
"""能力开关与市场端点（用户侧）。"""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DEV_AUTH_TOKEN", "devtok")
    app = create_app()
    with TestClient(app) as c:
        yield c


HEADERS = {"Authorization": "Bearer devtok"}


# 用真实的内置目录条目（catalog/skills/data-analysis）：
# 公共层技能不在 catalog 里，can_install 会 404，不能拿它当被安装对象
ITEM = "data-analysis"


def test_switch_enabled_without_install_is_422(client):
    resp = client.put(f"/api/v1/me/capabilities/skill/{ITEM}",
                      json={"enabled": True}, headers=HEADERS)
    assert resp.status_code == 422


def test_switch_install_then_disable(client):
    install = client.put(f"/api/v1/me/capabilities/skill/{ITEM}",
                         json={"installed": True}, headers=HEADERS)
    assert install.status_code == 200
    assert install.json() == {"kind": "skill", "id": ITEM,
                             "installed": True, "enabled": True}

    off = client.put(f"/api/v1/me/capabilities/skill/{ITEM}",
                     json={"enabled": False}, headers=HEADERS)
    assert off.status_code == 200
    assert off.json() == {"kind": "skill", "id": ITEM,
                          "installed": True, "enabled": False}

    # 停用后从可见性中被剔除（此任务用既有 /catalog 端点校验；/market 在 Task 9 提供）
    rows = client.get("/api/v1/catalog?kind=skill", headers=HEADERS).json()
    row = next(r for r in rows if r["id"] == ITEM)
    assert row["installed"] is True
    assert row["enabled"] is False
    assert row["visible"] is False
```

> **示例订正**：可安装对象必须落在 catalog 内置目录里（`can_install` 只认内置条目），
> 所以这里用真实条目 `data-analysis`，而不是临时 `POST /api/v1/skills` 建的公共层技能。

- [ ] **Step 2: 运行测试确认失败**

Run: `conda run -n synlysagent python -m pytest tests/test_catalog_api.py -v`
Expected: FAIL —— 404（`PUT /api/v1/me/capabilities/...` 不存在），`/api/v1/me/skills` 也不存在。

- [ ] **Step 3: 实现端点（本任务先只做开关，`/me/skills` 在 Task 9）**

在 `catalog/api.py` 末尾追加：

```python
class CapabilitySwitchBody(BaseModel):
    """能力开关请求体（两个字段都可选，缺省表示不改）。"""

    installed: bool | None = None
    enabled: bool | None = None


@router.put("/me/capabilities/{kind}/{item_id}")
async def switch_capability(kind: str, item_id: str, body: CapabilitySwitchBody,
                            request: Request,
                            user=Depends(get_current_user),
                            service=Depends(get_capability_service)) -> dict:
    """安装/卸载/启用/停用某条目（用户维度）。

    `installed=true` 等价安装（先过 can_install 判定），`installed=false` 等价卸载
    （删记录）；`enabled` 只对已安装条目有效，未安装时返回 422。

    Args:
        kind: 条目类型。
        item_id: 条目 id。
        body: 开关请求体。
        request: FastAPI 请求。
        user: 当前用户。
        service: 能力服务。

    Returns:
        {"kind", "id", "installed", "enabled"}。

    Raises:
        HTTPException: 类型或条目非法（404）、未安装却要改启用态（422）。
    """
    if kind not in KINDS:
        raise HTTPException(404, f"未知类型: {kind}")
    user_id = user["sub"]
    if body.installed is True:
        if not await service.can_install(user_id, kind, item_id):
            raise HTTPException(404, f"条目不可安装: {kind}:{item_id}")
        await service.installs.install(user_id, kind, item_id)
        if kind == "plugin":
            plugin_service = getattr(request.app.state, "plugin_service", None)
            if plugin_service is not None:
                plugin_service.ensure_attached(item_id)
    elif body.installed is False:
        await service.installs.uninstall(user_id, kind, item_id)
    # 卸载请求里的 enabled 一并视为无效：先卸载就没有记录可改，否则
    # {"installed": false, "enabled": false} 这种自洽请求会被误判成 422
    if body.enabled is not None and body.installed is not False:
        if not await service.installs.set_enabled(user_id, kind, item_id, body.enabled):
            raise HTTPException(422, f"未安装，无法设置启用态: {kind}:{item_id}")
    return {
        "kind": kind, "id": item_id,
        "installed": await service.installs.is_installed(user_id, kind, item_id),
        "enabled": await service.installs.is_enabled(user_id, kind, item_id),
    }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `conda run -n synlysagent python -m pytest tests/test_catalog_api.py -v`
Expected: PASS（两个用例都只依赖本次新增的开关端点与既有 `/catalog` 端点）。

- [ ] **Step 5: 提交**

```bash
git add apps/web/backend/app/catalog/api.py apps/web/backend/tests/test_catalog_api.py
git commit -m "feat: 能力开关端点（安装/卸载/启用/停用）"
```

---

### Task 9: 市场端点 GET /api/v1/market/{kind}

**Files:**
- Modify: `apps/web/backend/app/catalog/api.py`
- Test: `apps/web/backend/tests/test_catalog_api.py`

- [ ] **Step 1: 写测试**

追加：

```python
def test_market_lists_items_with_state(client):
    """市场列表返回条目及其安装/启用/可见状态。"""
    rows = client.get("/api/v1/market/skill", headers=HEADERS).json()
    row = next(r for r in rows if r["id"] == ITEM)   # ITEM = "data-analysis"（内置条目）
    # 下面三行只是"平台缺省值快照"（public + 非默认启用 + 未安装），实现里写死
    # 常量也能通过，判别力弱；本用例真正有判别力的断言是 `visible is False`
    # （缺省不可见）。默认启用那半边分支见 test_market_item_visible_when_default_enabled。
    assert row["installed"] is False
    assert row["enabled"] is False
    assert row["visible"] is False
    assert row["visibility"] == "public"
    assert row["default_enabled"] is False

    client.put(f"/api/v1/me/capabilities/skill/{ITEM}",
               json={"installed": True}, headers=HEADERS)
    row = next(r for r in client.get("/api/v1/market/skill", headers=HEADERS).json()
               if r["id"] == ITEM)
    assert row["installed"] is True and row["enabled"] is True and row["visible"] is True
```

> **示例订正**：同 Task 8——市场列表里的可安装对象来自 catalog 内置条目（此处 `data-analysis`），
> 不能拿 `POST /api/v1/skills` 建的公共层技能充当，否则 `can_install` 判 404。

- [ ] **Step 2: 运行测试确认失败**

Run: `conda run -n synlysagent python -m pytest tests/test_catalog_api.py::test_market_lists_items_with_state -v`
Expected: FAIL —— 404（无 `/api/v1/market/{kind}`）。

- [ ] **Step 3: 实现**

`catalog/api.py` 追加：

```python
@router.get("/market/{kind}")
async def market(kind: str, user=Depends(get_current_user),
                 service=Depends(get_capability_service)) -> list[dict]:
    """市场列表（某类型下当前用户可见的可安装条目）。

    Args:
        kind: 条目类型（expert/skill/plugin）。
        user: 当前用户。
        service: 能力服务。

    Returns:
        条目列表（含 installed/enabled/default_enabled/visibility）。

    Raises:
        HTTPException: 类型非法（404）。
    """
    if kind not in KINDS:
        raise HTTPException(404, f"未知类型: {kind}")
    return await service.market_items(user["sub"], kind)
```

（既有的 `GET /api/v1/catalog` 保留不动，前端迁移完成后再单独清理。）

- [ ] **Step 4: 运行测试确认通过**

Run: `conda run -n synlysagent python -m pytest tests/test_catalog_api.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add apps/web/backend/app/catalog/api.py apps/web/backend/tests/test_catalog_api.py
git commit -m "feat: 市场端点 /api/v1/market/{kind}"
```

---

## Phase 3 — 用户级技能

### Task 10: SkillService 支持用户根 + 同名拒绝

**Files:**
- Modify: `apps/web/backend/app/services/skill_service.py`
- Test: `apps/web/backend/tests/test_skill_service.py`

- [ ] **Step 1: 写测试**

追加：

```python
def test_user_skills_are_scoped_to_owner(tmp_path):
    svc = SkillService(tmp_path)
    svc.write_user_skill("u1", name="mine", description="我的", content="## 目标\nx")
    assert [s["name"] for s in svc.list_own_skills("u1")] == ["mine"]
    assert svc.list_own_skills("u2") == []
    assert (tmp_path / "users" / "u1" / "skills" / "mine" / "SKILL.md").is_file()


def test_user_skill_takes_precedence_over_public(tmp_path):
    svc = SkillService(tmp_path)
    svc.write_skill(name="dup", description="公共", content="## 目标\n公共")
    svc.write_user_skill("u1", name="dup2", description="我的", content="## 目标\n我的")
    names = [s["name"] for s in svc.list_skills(user_id="u1")]
    assert "dup" in names and "dup2" in names


def test_write_user_skill_rejects_taken_name(tmp_path):
    svc = SkillService(tmp_path)
    svc.write_skill(name="taken", description="公共", content="## 目标\nx")
    with pytest.raises(SkillNameTaken):
        svc.write_user_skill("u1", name="taken", description="d", content="c")


def test_delete_user_skill_only_affects_own(tmp_path):
    svc = SkillService(tmp_path)
    svc.write_user_skill("u1", name="mine", description="d", content="c")
    assert svc.delete_user_skill("u2", "mine") is False
    assert svc.delete_user_skill("u1", "mine") is True


def test_read_body_prefers_user_copy(tmp_path):
    svc = SkillService(tmp_path)
    svc.write_skill(name="shared", description="公共", content="## 目标\n公共正文")
    svc.write_user_skill("u1", name="mine", description="我的", content="## 目标\n我的正文")
    assert "我的正文" in svc.read_body("mine", user_id="u1")
```

文件顶部 import 补 `import pytest` 与 `from app.services.skill_service import SkillService, SkillNameTaken`
（若已有 import 行则合并）。

- [ ] **Step 2: 运行测试确认失败**

Run: `conda run -n synlysagent python -m pytest tests/test_skill_service.py -v`
Expected: FAIL —— `SkillNameTaken` 不存在，`write_user_skill`/`list_own_skills`/`delete_user_skill` 不存在。

- [ ] **Step 3: 实现**

`skill_service.py` 中：

1) 类前新增异常：

```python
class SkillNameTaken(ValueError):
    """技能名已被占用（用户自建 / 公共层 / 内置任一来源）。

    继承 `ValueError`，调用方既有的 `except ValueError` 仍可兜住；API 层优先映射 409。
    """
```

2) `SkillService` 增加用户根与相关方法（放在 `skills_dir` 属性之后）：

```python
    def user_skills_dir(self, user_id: str) -> Path:
        """某用户的自建技能根（不存在时不创建）。

        Args:
            user_id: 用户 sub。

        Returns:
            {data_root}/users/{user_id}/skills 路径。
        """
        return workspace_skills_root(self._data_root, user_id)

    def list_own_skills(self, user_id: str) -> list[dict]:
        """扫描某用户自建技能。

        Args:
            user_id: 用户 sub。

        Returns:
            技能字典列表（builtin 恒为 False）。
        """
        return self._scan_root(self.user_skills_dir(user_id), builtin=False)

    def name_taken(self, name: str) -> str | None:
        """技能名是否已被任一来源占用。

        Args:
            name: 技能名。

        Returns:
            占用来源（"public" / "builtin"），未占用返回 None。
        """
        if (self.skills_dir / name / "SKILL.md").is_file():
            return "public"
        for root in self._extra_roots:
            if (root / name / "SKILL.md").is_file():
                return "builtin"
        return None

    def write_user_skill(self, user_id: str, *, name: str, description: str,
                         content: str, version: str = "1.0", author: str = "",
                         tags: list[str] | None = None,
                         allowed_tools: list[str] | None = None) -> dict:
        """写入某用户的自建技能（同名占用则拒绝）。

        Args:
            user_id: 用户 sub。
            name: 技能名（kebab-case，同时作为目录名）。
            description: 技能描述。
            content: 正文。
            version: 版本号。
            author: 作者。
            tags: 标签列表。
            allowed_tools: 允许的工具名列表。

        Returns:
            写入后的技能字典（builtin 恒为 False）。

        Raises:
            ValueError: 技能名不是 kebab-case。
            SkillNameTaken: 名字已被占用（自建 / 公共 / 内置）。
        """
        if not NAME_OK.match(name):
            raise ValueError(f"技能名必须是 kebab-case: {name!r}")
        if (self.user_skills_dir(user_id) / name / "SKILL.md").is_file():
            raise SkillNameTaken(f"你已有同名技能「{name}」")
        origin = self.name_taken(name)
        if origin is not None:
            label = "公共技能" if origin == "public" else "内置技能"
            raise SkillNameTaken(f"「{name}」与{label}同名，请换一个名字")
        skill = {"name": name, "description": description, "content": content,
                 "version": version, "author": author,
                 "tags": tags or [], "allowed_tools": allowed_tools or []}
        target = self.user_skills_dir(user_id) / name
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(render_skill_md(skill), encoding="utf-8")
        return {**skill, "builtin": False}

    def delete_user_skill(self, user_id: str, name: str) -> bool:
        """删除某用户的自建技能目录。

        Args:
            user_id: 用户 sub。
            name: 技能名。

        Returns:
            存在并已删除返回 True；名字非法或不存在返回 False。
        """
        if not NAME_OK.match(name):
            return False
        target = self.user_skills_dir(user_id) / name
        if not target.is_dir():
            return False
        shutil.rmtree(target)
        return True
```

3) `list_skills` / `read_body` 增加可选 `user_id`（默认 None 表示不并入用户根）：

```python
    def list_skills(self, user_id: str | None = None) -> list[dict]:
        """扫描全部技能（用户根 → 公共层 → 只读根，前者同名优先）。

        Args:
            user_id: 用户 sub；None 表示不含任何用户根（管理员全局视图）。

        Returns:
            技能字典列表，每项含 `builtin` 标记。
        """
        out = self._scan_root(self.user_skills_dir(user_id), builtin=False) if user_id else []
        seen = {s["name"] for s in out}
        for skill in self._scan_root(self.skills_dir, builtin=False):
            if skill["name"] not in seen:
                seen.add(skill["name"])
                out.append(skill)
        for root in self._extra_roots:
            for skill in self._scan_root(root, builtin=True):
                if skill["name"] not in seen:
                    seen.add(skill["name"])
                    out.append(skill)
        return out

    def read_body(self, name: str, user_id: str | None = None) -> str | None:
        """读技能正文（用户根优先于公共层与只读根）。

        Args:
            name: 技能名（即目录名）。
            user_id: 用户 sub；None 表示不含用户根。

        Returns:
            正文文本；名字非法、技能不存在或不可解析时返回 None。
        """
        if not NAME_OK.match(name):
            return None
        roots = ([self.user_skills_dir(user_id)] if user_id else []) + \
            [self.skills_dir, *self._extra_roots]
        for root in roots:
            md = root / name / "SKILL.md"
            if not md.is_file():
                continue
            try:
                return parse_skill_md(md.read_text(encoding="utf-8"))["content"]
            except (ValueError, yaml.YAMLError):
                continue
        return None
```

4) 顶部 import 增加工作区路径函数：

```python
from app.services.workspace import user_skills_root as workspace_skills_root
```

（用别名避免与 `user_skills_dir` 方法名混淆。）

- [ ] **Step 4: 运行测试确认通过**

Run: `conda run -n synlysagent python -m pytest tests/test_skill_service.py tests/test_skills_api.py tests/test_plugin_service.py -v`
Expected: PASS（`list_skills()` 无参调用行为不变，插件技能根用例不受影响）。

- [ ] **Step 5: 提交**

```bash
git add apps/web/backend/app/services/skill_service.py apps/web/backend/tests/test_skill_service.py
git commit -m "feat: 技能服务支持用户自建根与同名拒绝"
```

---

### Task 11: 我的技能 API（/api/v1/me/skills）

**Files:**
- Create: `apps/web/backend/app/api/me_api.py`
- Modify: `apps/web/backend/app/main.py:129-139`（注册路由）
- Test: `apps/web/backend/tests/test_me_api.py`（新建）

- [ ] **Step 1: 写测试（新建文件）**

```python
"""我的技能 / 专家端点。"""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DEV_AUTH_TOKEN", "devtok")
    app = create_app()
    with TestClient(app) as c:
        yield c


HEADERS = {"Authorization": "Bearer devtok"}
BODY = {"name": "my-skill", "description": "自建演示", "content": "## 目标\n演示"}


def test_create_and_list_my_skill(client):
    created = client.post("/api/v1/me/skills", json=BODY, headers=HEADERS)
    assert created.status_code == 201
    rows = client.get("/api/v1/me/skills", headers=HEADERS).json()
    row = next(r for r in rows if r["name"] == "my-skill")
    assert row["source"] == "mine" and row["enabled"] is True


def test_my_skill_name_conflict_with_public(client):
    client.post("/api/v1/skills", json={
        "name": "public-dup", "description": "公共", "content": "## 目标\nx"},
        headers=HEADERS)
    dup = client.post("/api/v1/me/skills", json={
        "name": "public-dup", "description": "我", "content": "## 目标\ny"}, headers=HEADERS)
    assert dup.status_code == 409


def test_update_and_delete_only_own(client):
    client.post("/api/v1/me/skills", json=BODY, headers=HEADERS)
    updated = client.patch("/api/v1/me/skills/my-skill", json={
        "description": "改过", "content": "## 目标\n改"}, headers=HEADERS)
    assert updated.status_code == 200 and updated.json()["description"] == "改过"
    assert client.delete("/api/v1/me/skills/my-skill", headers=HEADERS).status_code == 200
    assert client.delete("/api/v1/me/skills/my-skill", headers=HEADERS).status_code == 404


def test_cannot_edit_builtin_skill(client):
    resp = client.patch("/api/v1/me/skills/nonexistent", json={
        "description": "x", "content": "y"}, headers=HEADERS)
    assert resp.status_code == 403
```

- [ ] **Step 2: 运行测试确认失败**

Run: `conda run -n synlysagent python -m pytest tests/test_me_api.py -v`
Expected: FAIL —— 404（路由不存在）。

- [ ] **Step 3: 实现**

创建 `app/api/me_api.py`：

```python
"""「我的」端点：用户自建技能（后续任务在此文件追加专家）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.services.skill_service import SkillNameTaken

router = APIRouter(prefix="/api/v1/me", tags=["me"])


class MySkillBody(BaseModel):
    """自建技能字段（content 为 SKILL.md 正文，不含 frontmatter）。"""

    name: str
    description: str
    content: str
    version: str = "1.0"
    author: str = ""
    tags: list[str] = []
    allowed_tools: list[str] = []


class MySkillUpdateBody(BaseModel):
    """自建技能更新体（技能名以路径参数为准）。"""

    description: str
    content: str
    version: str = "1.0"
    author: str = ""
    tags: list[str] = []
    allowed_tools: list[str] = []


def _skill_service(request: Request):
    """取 SkillService（未就绪 503）。"""
    service = getattr(request.app.state, "skill_service", None)
    if service is None:
        raise HTTPException(503, "技能服务未就绪")
    return service


@router.get("/skills")
async def list_my_skills(request: Request, user=Depends(get_current_user)) -> list[dict]:
    """我的技能 = 自建 ∪ 已安装的内置技能。

    Args:
        request: FastAPI 请求。
        user: 当前用户。

    Returns:
        条目列表（source = mine|installed，含 enabled）。
    """
    svc = _skill_service(request)
    caps = getattr(request.app.state, "capability_service", None)
    user_id = user["sub"]
    rows = [
        {"name": s["name"], "description": s["description"],
         "version": s.get("version", "1.0"), "source": "mine",
         "installed": False, "enabled": True, "builtin": False}
        for s in svc.list_own_skills(user_id)
    ]
    if caps is not None:
        installed = set(await caps.installs.list_for_user(user_id, kind="skill"))
        enabled = await caps.installs.enabled_item_ids(user_id, "skill")
        hidden = {i.id for i in caps.catalog.list_items("skill")} - await caps.visible_ids(
            user_id, "skill")
        for item in caps.catalog.list_items("skill"):
            if f"skill:{item.id}" not in installed:
                continue
            rows.append({
                "name": item.id, "description": item.description,
                "version": "1.0", "source": "installed",
                "installed": True, "enabled": item.id in enabled,
                "builtin": True, "revoked": item.id in hidden,
            })
    return rows


@router.post("/skills", status_code=201)
async def create_my_skill(request: Request, body: MySkillBody,
                          user=Depends(get_current_user)) -> dict:
    """新建自建技能。

    Raises:
        HTTPException: 422 名字不合法；409 与已有/公共/内置技能同名。
    """
    try:
        return _skill_service(request).write_user_skill(
            user["sub"], name=body.name, description=body.description,
            content=body.content, version=body.version, author=body.author,
            tags=body.tags, allowed_tools=body.allowed_tools)
    except SkillNameTaken as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.patch("/skills/{name}")
async def update_my_skill(request: Request, name: str, body: MySkillUpdateBody,
                          user=Depends(get_current_user)) -> dict:
    """覆盖写自建技能（非自建 → 403）。

    Raises:
        HTTPException: 403 不是自建技能；422 名字不合法。
    """
    svc = _skill_service(request)
    user_id = user["sub"]
    if not (svc.user_skills_dir(user_id) / name / "SKILL.md").is_file():
        raise HTTPException(403, "只能修改自己创建的技能")
    try:
        return svc.write_user_skill(
            user_id, name=name, description=body.description, content=body.content,
            version=body.version, author=body.author,
            tags=body.tags, allowed_tools=body.allowed_tools)
    except SkillNameTaken as exc:  # 覆盖自己的技能不该触发，防御性兜底
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/skills/{name}")
async def delete_my_skill(request: Request, name: str,
                          user=Depends(get_current_user)) -> dict:
    """删除自建技能。

    Raises:
        HTTPException: 404 不存在（含内置/公共技能——它们不在用户目录里）。
    """
    if not _skill_service(request).delete_user_skill(user["sub"], name):
        raise HTTPException(404, "技能不存在")
    return {"ok": True}
```

`main.py`：在 import 区加 `from app.api.me_api import router as me_router`，
在 `app.include_router(catalog_router)` 之后加 `app.include_router(me_router)`。

- [ ] **Step 4: 运行测试确认通过**

Run: `conda run -n synlysagent python -m pytest tests/test_me_api.py tests/test_catalog_api.py -v`
Expected: PASS（Task 8 里若给 `test_capability_install_then_disable` 加了 xfail，此时移除）。

- [ ] **Step 5: 提交**

```bash
git add apps/web/backend/app/api/me_api.py apps/web/backend/app/main.py apps/web/backend/tests/test_me_api.py
git commit -m "feat: 我的技能端点（自建增删改查 + 已装列表）"
```

---

### Task 12: 运行期技能索引按用户过滤

**Files:**
- Modify: `apps/web/backend/app/services/agent_service.py:312`、`:325`
- Test: `apps/web/backend/tests/test_chat_api.py`

- [ ] **Step 1: 写测试**

在 `tests/test_chat_api.py` 追加（前置照该文件既有的
`test_multi_turn_events_persist_and_context`：造 provider → 绑助手 → `_make_session` → `_chat_once`）：

```python
async def test_chat_passes_user_id_to_skill_index(app, client, admin_headers, monkeypatch):
    """运行期技能索引必须按登录用户解析（用户自建技能才进得来）。"""
    svc = app.state.skill_service
    original = svc.list_skills
    seen: list[str | None] = []

    def spy(user_id=None):
        seen.append(user_id)
        return original(user_id=user_id)

    monkeypatch.setattr(svc, "list_skills", spy)
    await _make_provider(client, admin_headers)
    await _bind_provider_to_asst_data(client, admin_headers)
    sid = await _make_session(client, admin_headers)
    await _chat_once(client, admin_headers, sid)

    assert seen, "chat 未调用技能索引"
    assert all(uid for uid in seen), f"技能索引未带 user_id: {seen}"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `conda run -n synlysagent python -m pytest tests/test_chat_api.py -v`
Expected: FAIL —— 用户自建技能不在索引里（`list_skills()` 未传 user_id）。

- [ ] **Step 3: 实现**

`agent_service.py` 的 `chat()` 里两处改为带 `user_id`：

```python
            all_skills = self._skill_service.list_skills(user_id=str(user.get("sub") or ""))
```

```python
            bodies = {s["name"]: self._skill_service.read_body(
                s["name"], user_id=str(user.get("sub") or "")) or ""
                for s in all_skills}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `conda run -n synlysagent python -m pytest tests/test_chat_api.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add apps/web/backend/app/services/agent_service.py apps/web/backend/tests/test_chat_api.py
git commit -m "feat: 运行期技能索引并入用户自建技能"
```

---

## Phase 4 — 用户级专家

### Task 13: 用户专家文件服务（读写 + 列出）

**Files:**
- Create: `apps/web/backend/app/services/expert_service.py`
- Test: `apps/web/backend/tests/test_expert_service.py`（新建）

- [ ] **Step 1: 写测试（新建文件）**

```python
"""用户自建专家（文件为事实源 + 实例化进 assistants）。"""
import json
from pathlib import Path

import pytest

from app.services.expert_service import UserExpertService


@pytest.fixture()
def svc(tmp_path, store):
    return UserExpertService(store, tmp_path)


async def test_write_and_list_own_expert(svc, tmp_path):
    expert = await svc.write("u1", dir_name="chem", name="化学助手", avatar="🧪",
                             description="演示", system_prompt="你是化学助手",
                             tool_whitelist=["python.run"])
    assert expert["_id"] == "u1:chem"
    path = tmp_path / "users" / "u1" / "experts" / "chem" / "expert.json"
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "化学助手"
    own = await svc.list_own("u1")
    assert [e["_id"] for e in own] == ["u1:chem"]
    assert await svc.list_own("u2") == []


async def test_experts_are_scoped_to_owner(svc):
    await svc.write("u1", dir_name="chem", name="化学助手", avatar="",
                    description="demo", system_prompt="p")
    assert await svc.get_own("u1", "u1:chem") is not None
    assert await svc.get_own("u2", "u1:chem") is None


async def test_delete_removes_file_and_record(svc, tmp_path, store):
    await svc.write("u1", dir_name="chem", name="化学助手", avatar="",
                    description="demo", system_prompt="p")
    await svc.ensure_instantiated("u1")
    assert await store.get("assistants", "u1:chem") is not None
    assert await svc.delete("u1", "u1:chem") is True
    assert not (tmp_path / "users" / "u1" / "experts" / "chem").exists()
    assert await store.get("assistants", "u1:chem") is None


async def test_instantiate_is_idempotent(svc, store):
    await svc.write("u1", dir_name="chem", name="化学助手", avatar="",
                    description="demo", system_prompt="p")
    await svc.ensure_instantiated("u1")
    await store.update("assistants", "u1:chem", {"name": "管理员改过"})
    await svc.ensure_instantiated("u1")  # 已存在 → 不覆盖
    assert (await store.get("assistants", "u1:chem"))["name"] == "管理员改过"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `conda run -n synlysagent python -m pytest tests/test_expert_service.py -v`
Expected: FAIL —— `app.services.expert_service` 不存在。

- [ ] **Step 3: 实现**

创建 `app/services/expert_service.py`：

```python
"""用户自建专家：文件为事实源，读时幂等实例化进 assistants 集合。

文件布局：{data_root}/users/{user_id}/experts/{dir}/expert.json
文档形态与 catalog/experts 的内置专家一致（见 catalog/loader.py 的 ExpertPackage），
但多一个由平台维护的 `_id`（形如 `{user_id}:{dir}`）与 `owner` 字段。

写入纪律：**先动文件再刷记录**（照 ProjectService.rename_project），失败即整体失败，
避免"文件与记录不一致"的半成品状态。
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

EXPERT_MANIFEST = "expert.json"
_SAFE_DIR = re.compile(r"[^A-Za-z0-9_一-鿿-]+")


class UserExpertService:
    """用户自建专家的文件读写与助手实例化。"""

    def __init__(self, store: Any, data_root: Path) -> None:
        """保存依赖。

        Args:
            store: DocumentStore 实例。
            data_root: 数据根目录。
        """
        self._store = store
        self._data_root = data_root

    def experts_dir(self, user_id: str) -> Path:
        """某用户的专家目录（不创建）。

        Args:
            user_id: 用户 sub。

        Returns:
            {data_root}/users/{user_id}/experts 路径。
        """
        return self._data_root / "users" / user_id / "experts"

    @staticmethod
    def expert_id(user_id: str, dir_name: str) -> str:
        """专家文档 id。

        Args:
            user_id: 用户 sub。
            dir_name: 目录名。

        Returns:
            f"{user_id}:{dir_name}"。
        """
        return f"{user_id}:{dir_name}"

    @staticmethod
    def sanitize_dir_name(name: str) -> str:
        """把显示名安全化为目录名。

        Args:
            name: 专家显示名。

        Returns:
            只含字母/数字/下划线/连字符/中文的名字；全被过滤时返回空串。
        """
        return _SAFE_DIR.sub("_", name.strip()).strip("_")

    def _manifest_path(self, user_id: str, dir_name: str) -> Path:
        """expert.json 路径。

        Args:
            user_id: 用户 sub。
            dir_name: 目录名。

        Returns:
            {data_root}/users/{uid}/experts/{dir}/expert.json。
        """
        return self.experts_dir(user_id) / dir_name / EXPERT_MANIFEST

    def _load(self, user_id: str, dir_name: str) -> dict | None:
        """读单个专家文件。

        Args:
            user_id: 用户 sub。
            dir_name: 目录名。

        Returns:
            含 _id/name/avatar/description/system_prompt/tool_whitelist/dir_name 的字典；
            文件缺失或损坏返回 None。
        """
        path = self._manifest_path(user_id, dir_name)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("用户专家 manifest 解析失败，已跳过 %s", path)
            return None
        if not isinstance(data, dict) or not str(data.get("system_prompt") or "").strip():
            logger.warning("用户专家 manifest 非法（缺 system_prompt），已跳过 %s", path)
            return None
        return {
            "_id": self.expert_id(user_id, dir_name),
            "dir_name": dir_name,
            "name": str(data.get("name") or dir_name),
            "avatar": str(data.get("avatar") or ""),
            "description": str(data.get("description") or ""),
            "system_prompt": str(data["system_prompt"]),
            "tool_whitelist": [str(t) for t in (data.get("tool_whitelist") or [])],
        }

    async def list_own(self, user_id: str) -> list[dict]:
        """列出某用户的自建专家。

        Args:
            user_id: 用户 sub。

        Returns:
            专家字典列表（按目录名排序）。
        """
        root = self.experts_dir(user_id)
        if not root.is_dir():
            return []
        out: list[dict] = []
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            expert = self._load(user_id, entry.name)
            if expert is not None:
                out.append(expert)
        return out

    async def get_own(self, user_id: str, expert_id: str) -> dict | None:
        """取某用户的自建专家（校验归属）。

        Args:
            user_id: 用户 sub。
            expert_id: 专家 id（形如 {user_id}:{dir}）。

        Returns:
            专家字典；不存在或不属于该用户返回 None。
        """
        prefix = f"{user_id}:"
        if not expert_id.startswith(prefix):
            return None
        return self._load(user_id, expert_id[len(prefix):])

    async def write(self, user_id: str, *, dir_name: str, name: str, avatar: str,
                    description: str, system_prompt: str,
                    tool_whitelist: list[str] | None = None) -> dict:
        """写入（新建或覆盖）一个自建专家：先写文件，再刷 assistants 记录。

        Args:
            user_id: 用户 sub。
            dir_name: 目录名（sanitize 后的名字）。
            name: 显示名。
            avatar: 头像 emoji。
            description: 描述。
            system_prompt: 人设提示词。
            tool_whitelist: 可用工具名列表。

        Returns:
            专家字典（含 _id）。

        Raises:
            ValueError: dir_name 为空或不合法。
        """
        clean = self.sanitize_dir_name(dir_name)
        if not clean:
            raise ValueError("专家名不合法")
        payload = {
            "name": name.strip() or clean,
            "avatar": avatar,
            "description": description,
            "system_prompt": system_prompt,
            "tool_whitelist": [str(t) for t in (tool_whitelist or [])],
        }
        target = self.experts_dir(user_id) / clean
        target.mkdir(parents=True, exist_ok=True)
        (target / EXPERT_MANIFEST).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        expert = {**payload, "_id": self.expert_id(user_id, clean), "dir_name": clean}
        await self._upsert_record(user_id, expert)
        return expert

    async def delete(self, user_id: str, expert_id: str) -> bool:
        """删除自建专家（先删文件目录，再删记录）。

        Args:
            user_id: 用户 sub。
            expert_id: 专家 id。

        Returns:
            True 表示已删除；专家不存在返回 False。
        """
        expert = await self.get_own(user_id, expert_id)
        if expert is None:
            return False
        shutil.rmtree(self.experts_dir(user_id) / expert["dir_name"], ignore_errors=True)
        await self._store.delete("assistants", expert_id)
        return True

    async def ensure_instantiated(self, user_id: str) -> list[dict]:
        """把该用户的自建专家幂等实例化进 assistants（列「我的专家」时调用）。

        Args:
            user_id: 用户 sub。

        Returns:
            该用户的自建专家列表（含 _id）。
        """
        experts = await self.list_own(user_id)
        for expert in experts:
            await self._upsert_record(user_id, expert)
        return experts

    async def _upsert_record(self, user_id: str, expert: dict) -> None:
        """按 _id 幂等写 assistants 记录（已存在则刷新内容）。

        Args:
            user_id: 用户 sub。
            expert: 专家字典。
        """
        body = {
            "name": expert["name"], "avatar": expert["avatar"],
            "description": expert["description"],
            "system_prompt": expert["system_prompt"],
            "tool_whitelist": list(expert["tool_whitelist"]),
            "model_provider_id": None, "knowledge_base_ids": [],
            "builtin": False, "owner": user_id, "source_dir": expert["dir_name"],
        }
        if await self._store.get("assistants", expert["_id"]) is None:
            await self._store.insert("assistants", {"_id": expert["_id"], **body})
        else:
            await self._store.update("assistants", expert["_id"], body)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `conda run -n synlysagent python -m pytest tests/test_expert_service.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add apps/web/backend/app/services/expert_service.py apps/web/backend/tests/test_expert_service.py
git commit -m "feat: 用户自建专家文件服务与助手实例化"
```

---

### Task 14: 我的专家 API + 装配

**Files:**
- Modify: `apps/web/backend/app/api/me_api.py`
- Modify: `apps/web/backend/app/main.py`（构造并挂到 `app.state`）
- Test: `apps/web/backend/tests/test_me_api.py`

- [ ] **Step 1: 写测试**

在 `tests/test_me_api.py` 追加：

```python
EXPERT = {"name": "化学助手", "avatar": "🧪", "description": "演示",
          "system_prompt": "你是化学助手", "tool_whitelist": ["python.run"]}


def test_create_list_and_delete_my_expert(client):
    created = client.post("/api/v1/me/experts", json=EXPERT, headers=HEADERS)
    assert created.status_code == 201
    expert_id = created.json()["id"]
    rows = client.get("/api/v1/me/experts", headers=HEADERS).json()
    assert any(r["id"] == expert_id and r["source"] == "mine" for r in rows)
    assert client.delete(f"/api/v1/me/experts/{expert_id}",
                         headers=HEADERS).status_code == 200
    assert all(r["id"] != expert_id for r in
               client.get("/api/v1/me/experts", headers=HEADERS).json())
```

- [ ] **Step 2: 运行测试确认失败**

Run: `conda run -n synlysagent python -m pytest tests/test_me_api.py -v`
Expected: FAIL —— 404（`/me/experts` 不存在）。

- [ ] **Step 3: 实现**

`main.py` 构造（放在 `app.state.project_service` 之后）：

```python
    app.state.expert_service = UserExpertService(store, settings.data_root)
```

并在 import 区加 `from app.services.expert_service import UserExpertService`。

`me_api.py` 追加（专家部分）：

```python
class MyExpertBody(BaseModel):
    """自建专家字段。"""

    name: str
    avatar: str = ""
    description: str = ""
    system_prompt: str
    tool_whitelist: list[str] = []


def _expert_service(request: Request):
    """取 UserExpertService（未就绪 503）。"""
    service = getattr(request.app.state, "expert_service", None)
    if service is None:
        raise HTTPException(503, "专家服务未就绪")
    return service


@router.get("/experts")
async def list_my_experts(request: Request, user=Depends(get_current_user)) -> list[dict]:
    """我的专家 = 自建 ∪ 已安装的内置专家。

    Args:
        request: FastAPI 请求。
        user: 当前用户。

    Returns:
        条目列表（source = mine|installed，含 enabled）。
    """
    svc = _expert_service(request)
    caps = getattr(request.app.state, "capability_service", None)
    user_id = user["sub"]
    own = await svc.ensure_instantiated(user_id)
    rows = [
        {"id": e["_id"], "name": e["name"], "avatar": e["avatar"],
         "description": e["description"], "source": "mine",
         "installed": False, "enabled": True, "builtin": False}
        for e in own
    ]
    if caps is not None:
        installed = set(await caps.installs.list_for_user(user_id, kind="expert"))
        enabled = await caps.installs.enabled_item_ids(user_id, "expert")
        for item in caps.catalog.list_items("expert"):
            if f"expert:{item.id}" not in installed:
                continue
            rows.append({
                "id": item.id, "name": item.name, "avatar": "",
                "description": item.description, "source": "installed",
                "installed": True, "enabled": item.id in enabled, "builtin": True,
            })
    return rows


@router.post("/experts", status_code=201)
async def create_my_expert(request: Request, body: MyExpertBody,
                           user=Depends(get_current_user)) -> dict:
    """新建自建专家。

    Raises:
        HTTPException: 422 名字不合法。
    """
    svc = _expert_service(request)
    user_id = user["sub"]
    try:
        expert = await svc.write(
            user_id, dir_name=body.name, name=body.name, avatar=body.avatar,
            description=body.description, system_prompt=body.system_prompt,
            tool_whitelist=body.tool_whitelist)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"id": expert["_id"], "name": expert["name"]}


@router.patch("/experts/{expert_id}")
async def update_my_expert(request: Request, expert_id: str, body: MyExpertBody,
                           user=Depends(get_current_user)) -> dict:
    """覆盖写自建专家。

    Raises:
        HTTPException: 404 不是自建专家；422 名字不合法。
    """
    svc = _expert_service(request)
    user_id = user["sub"]
    current = await svc.get_own(user_id, expert_id)
    if current is None:
        raise HTTPException(404, "专家不存在或不可编辑")
    try:
        expert = await svc.write(
            user_id, dir_name=current["dir_name"], name=body.name,
            avatar=body.avatar, description=body.description,
            system_prompt=body.system_prompt, tool_whitelist=body.tool_whitelist)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"id": expert["_id"], "name": expert["name"]}


@router.delete("/experts/{expert_id}")
async def delete_my_expert(request: Request, expert_id: str,
                           user=Depends(get_current_user)) -> dict:
    """删除自建专家。

    Raises:
        HTTPException: 404 不存在。
    """
    if not await _expert_service(request).delete(user["sub"], expert_id):
        raise HTTPException(404, "专家不存在")
    return {"ok": True}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `conda run -n synlysagent python -m pytest tests/test_me_api.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add apps/web/backend/app/api/me_api.py apps/web/backend/app/main.py apps/web/backend/tests/test_me_api.py
git commit -m "feat: 我的专家端点（自建增删改查 + 已装列表）"
```

---

### Task 15: 全量回归与文档同步

**Files:**
- Modify: `CLAUDE.md`（关键架构约定里的数据目录与能力中心描述）
- Modify: `apps/web/backend/app/version.py`（次版本 +1）

- [ ] **Step 1: 跑后端全量测试**

Run: `cd apps/web/backend && conda run -n synlysagent python -m pytest -v`
Expected: PASS。若有失败，先修到全绿再继续（不跳过、不 xfail 掩盖）。

- [ ] **Step 2: 更新 CLAUDE.md**

**顺带订正路径口径过时的注释与文档**（T1–T4 迁移后遗留，逐条 grep 确认后改）：
`app/api/skills_api.py` 里「写 `{data_root}/skills/<name>/SKILL.md`」→ `public/skills/...`；
`app/catalog/items.py` 的「数据目录根 `{data_dir}/catalog/`」→ `{data_dir}/public/catalog/`；
`apps/web/backend/README.md` 与仓库根 `README.md` 里 `{data_dir}/skills`、`workspaces/` 等旧口径，
以及 README 中 `user_capabilities` 集合的 schema 行（补 `enabled` 字段）；
`CLAUDE.md` 与仓库根 `README.md` 里「缺省 = public + 默认启用」的旧口径（现已改为"需安装"）；
**计划自身的订正**：Task 8/9 的示例测试原先拿公共层技能（`POST /api/v1/skills` 建的）当可安装对象，
但 `can_install` 只认 catalog 里的内置条目，实际实现已改用真实条目（如 `data-analysis`）——
把计划里这两处示例同步改过来，避免后来者照抄；
`CLAUDE.md` 中「内置内容统一在宿主 `catalog/`…」一节按 Task 15 Step 2 补用户分层与安装模型说明。

把"内置内容统一在宿主 catalog/ …"一节中的路径描述补上用户层：

```markdown
- 运行数据按用户分层：`{data_dir}/public/{skills,catalog}`（公共层）+ `{data_dir}/users/<uid>/{workspaces,sessions,skills,experts}`（用户层）；内置内容始终单一来源（repo 的 `catalog/`），用户"安装"只写记录不复制文件
```

并把"能力目录（市场）"一节补一句：

```markdown
- 用户安装后可启用/停用（`user_capabilities.enabled`）；内置条目策略缺省为 public + **非默认启用**（需安装后才可用）
```

- [ ] **Step 3: 版本号按语义化规则 +1**

`app/version.py` 中 `APP_VERSION` 自增次版本号（如 `0.4.0` → `0.5.0`），
`APP_VERSION_LABEL` 同步。

- [ ] **Step 4: 提交**

```bash
git add CLAUDE.md apps/web/backend/app/version.py
git commit -m "docs: 同步数据目录分层与安装模型说明并升版本"
```

---

## 完成后

- 后端能力层就绪后，执行前端计划 `docs/superpowers/plans/2026-09-15-synlysagent-11-capability-center-ui.md`。
- 交付前用真实账号验一遍验收标准 1–4、6（见规格第 11 节）。
