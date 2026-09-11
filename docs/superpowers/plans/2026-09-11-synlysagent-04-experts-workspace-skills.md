# SynlysAgent Plan 4：专家入口 / 技能框架 / 项目工作区

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「专家（=现有助手）选择」从侧栏挪进输入框 `+` 菜单，补齐文件上传与技能，右栏升级为「项目工作区目录树」，并给运行时装上一套默认系统提示词 + 技能渐进披露。

**Architecture:** 三个可独立验收的阶段。**Phase C（项目/工作区）**改目录布局与文件 API，是地基；**Phase B（技能 + 提示词）**新增 skills 子系统、在 harness 里做提示词分段组合与 `skill.*` 工具；**Phase A（输入框）**是前端收口，把专家/上传/技能/模型按钮按 jiuwen 形态摆进 Composer。三个阶段各自后向兼容、各自可跑测试。

**Tech Stack:** FastAPI + 双后端 DocumentStore（sqlite/mongodb）、纯 Python harness（零 FastAPI 依赖）、React 19 + TS + Tailwind 4 + Zustand、conda 环境 `synlysagent`。

**参考源（照抄对象，动手前必读）:**
- `+` 菜单：`E:\agent_projects\jiuwenswarm\jiuwenswarm\channels\web\frontend\src\components\ChatPanel\InputArea.tsx:2936-3341`（portal + fixed 定位）
- 一级/二级菜单 CSS：同目录 `ChatPanel.css:1598`（`.chat-mode-select__menu`）、`1718`（`.chat-input-attach-menu`）、`1811-1840`（二级面板 `left: calc(100% + 11px)`）
- 底部功能行（模型选择 + 发送）：`InputArea.tsx:3568`
- 技能面板：`components\ChatPanel\SkillPickerPanel.tsx`；技能管理页 `components\SkillPanel\index.tsx`
- 提示词分段优先级：`agents\harness\common\prompt\prompt_builder.py` 的 `PromptPriority`（IDENTITY=10, TASK_EXECUTION=21, SKILLS=40, INPUT=60, OUTPUT=65, WORKSPACE=70）
- 技能索引模板：`openjiuwen\harness\prompts\sections\skills.py` 的 `SKILL_RAIL_ALL_MODE_HEADER_CN` / `build_skill_line`
- 目录树（懒加载 + 递归）：`E:\agent_projects\deepseek-harness\packages\client\ui-sidebar-files\src\client\FilesBody.tsx`
- 技能规范原文：`E:\agent_projects\jiuwenswarm\jiuwenswarm\resources\agent\workspace\skills\agent-creator\references\skill-spec.md`

---

## 设计决策（动手前请确认，有异议先提）

| # | 决策 | 理由 |
|---|---|---|
| D1 | **专家 = 现有 `assistants` 表**，不新建模型。`system_prompt` 语义变更为「专家 persona」，**追加**在平台默认提示词之后 | 现有表已有 name/avatar/description/system_prompt/tool_whitelist，与 jiuwen agent_template 同构；jiuwen 也是追加不是替换（`assembly.py:_merge_team_prompt`） |
| D2 | **技能 = 磁盘上的 `SKILL.md` 文件**：`{data_root}/skills/<name>/SKILL.md`（+ 可选 `scripts/ references/ assets/`）。**不入库** | 两个参考项目都是文件——jiuwen `resources/agent/workspace/skills/<name>/SKILL.md`、DSH `.agents/skills/<name>/SKILL.md`（支持 flat `<name>.md`）。元数据即 YAML frontmatter，后台只做读写壳 |
| D3 | **技能是全局的**（admin 管理，内置种子随包发布、首启拷贝到 `data_root/skills/`、不可删），不是每用户一份 | 与现有 assistants 权限模型一致；jiuwen 的 `source: builtin` 同构 |
| D9 | **项目目录名与项目名解耦**：`dir_name = sanitize(name)`，被占用（在盘上或仍被活跃项目引用）则自动加后缀 `-2`/`-3`；项目 `name` 不唯一、创建**永不因重名失败**。**删除 = 删记录 + 删目录**，删目录失败则改名为 `{dir_name}.trash-{ts}` 释放名字保住数据 | 避免「删记录留目录 → 重建同名静默继承旧文件」；避免 Windows 文件占用导致 `rmtree` 失败后的孤儿目录 |
| D4 | **项目目录 = `{data_root}/workspaces/{user_id}/{dir_name}/`**，内部保留 `files/ output/ tmp/` 三子目录 | 只是把现有 `workspaces/{uid}/` 下移一层，`python.run` 的 cwd=`tmp/`、`file.*` 根=项目根 语义全部不变 |
| D5 | **旧数据一次性迁移**：首次访问项目列表时，若用户无项目且存在旧 `files/output/tmp`，创建 `默认项目` 并把三目录移入 | 不丢用户现有文件；`os.replace` 同盘秒级完成 |
| D6 | **右栏目录树只读**（浏览 + 下载 + 上传），不提供 mkdir/rename/delete | DSH 也是只读（`workspace-files` RPC 无写接口），写文件交给 agent 工具；减少越权面 |
| D7 | **工具名用点号风格** `skill.list` / `skill.read`（jiuwen 叫 `list_skill` / `skill_tool`） | 与现有 `file.read` / `python.run` 命名一致；`tool_whitelist` 校验依赖 `_REGISTRY.names` |
| D8 | **技能注入 = 索引进提示词 + 正文按需读工具**（渐进披露），不是全量塞进 system prompt | jiuwen 的 `skill_retrieval_prompt_rail.py` 就是这个契约；省 token |

### 技能存储调研（D2 的依据）

| | 存储位置 | 形态 | 加载契约 |
|---|---|---|---|
| jiuwen | 内置 `jiuwenswarm/resources/agent/workspace/skills/<name>/`；用户 `~/.jiuwenswarm/agent/workspace/skills/<name>/` | `SKILL.md` + `scripts/` + `references/` + `assets/` | `SkillManager._scan_*` 扫描目录 → 解析 frontmatter → 索引进提示词 → 模型调 `skill_tool` 读正文 |
| DSH | `.agents/skills/<name>/SKILL.md`、`.dsh/skills/`（也支持 flat `<name>.md`） | 目录包或单文件 | `dsh-skill-filesystem` 扫描 + **监听**（增删改免重启）→ `dsh-tool-skill` 注入 catalog（名字 + 截断描述）→ 模型调 `skill` 工具读全文；用户可用 `/name` 直接调用 |

结论：**两家都是文件**，且都是「catalog 进提示词 + 正文按需加载」的渐进披露。本项目采用同一形态。`dsh-skill` 的「provider 注册表 + 多来源合并 + 重名优先级」本轮不做（单来源够用），但目录布局预留了多 root 的可能。

---

## 文件结构

```
packages/synlys-harness/src/synlys_harness/
├── prompts.py                      # 新建：默认提示词分段 + 组合器（纯函数，零依赖）
├── resources/skills/               # 新建：内置技能种子（随包发布，首启拷贝到 data_root/skills/）
│   ├── data-analysis/SKILL.md
│   └── pdf-extraction/SKILL.md
├── tools/builtin.py                # 改：新增 skill.list / skill.read
└── tools/__init__.py               # 改：导出新工具

apps/web/backend/app/
├── services/workspace.py           # 改：多项目布局 + 迁移
├── services/project_service.py     # 新建：项目 CRUD（目录名去重 / 删除带 trash 兜底）+ 目录树
├── services/skill_service.py       # 新建：SKILL.md 扫描/解析/写入（纯文件系统，不入库）
├── services/agent_service.py       # 改：提示词组合 / 技能注入 / project workspace_root
├── db/repos.py                     # 改：ProjectRepo（技能不进库）
├── api/projects_api.py             # 新建：/api/v1/projects（含目录树、文件子路由）
├── api/skills_api.py               # 新建：/api/v1/skills（读写磁盘 SKILL.md）
├── api/files_api.py                # 改：按 project_id 作用域化（保留旧路径兼容）
├── api/sessions_api.py             # 改：SessionCreateBody 增 project_id
└── main.py                         # 改：挂载新路由 + 种子技能

apps/web/frontend/src/
├── components/chat/
│   ├── Composer.tsx                # 改：+ 菜单、专家 chip、模型按钮移位
│   ├── AttachMenu.tsx              # 新建：+ 弹层（portal + 定位 + 外点关闭）
│   ├── ExpertPicker.tsx            # 新建：专家二级面板
│   ├── SkillPicker.tsx             # 新建：技能二级面板
│   ├── ProjectPicker.tsx           # 新建：项目选择/新建（输入框下方 chip）
│   └── ModelPicker.tsx             # 改：加 size 变体（底部行用）
├── components/rightbar/
│   ├── WorkspacePanel.tsx          # 新建：目录树（替代 FilesPanel）
│   └── FileTree.tsx                # 新建：递归树（懒加载）
├── components/sidebar/Sidebar.tsx  # 改：移除助手卡
├── components/admin/SkillsAdmin.tsx# 新建：技能管理页
├── stores/
│   ├── projects.ts                 # 新建
│   ├── skills.ts                   # 新建
│   └── assistants.ts               # 改：去掉 selectedId 语义，改为「当前会话专家」
└── App.tsx                         # 改：admin 路由加 skills
```

---

# Phase C：项目与工作区（地基，先做）

## Task C1: workspace.py 多项目布局 + 迁移

**Files:** Modify `apps/web/backend/app/services/workspace.py`；Test `apps/web/backend/tests/test_workspace_layout.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# apps/web/backend/tests/test_workspace_layout.py
from pathlib import Path
from app.services import workspace


def test_project_root_creates_subdirs(tmp_path: Path):
    root = workspace.project_root(tmp_path, "u1", "my-proj")
    assert root == tmp_path / "workspaces" / "u1" / "my-proj"
    for sub in ("files", "output", "tmp"):
        assert (root / sub).is_dir()


def test_sanitize_dir_name_rejects_escapes():
    assert workspace.sanitize_dir_name("../../etc") == "etc"
    assert workspace.sanitize_dir_name("a/b\\c") == "a_b_c"
    assert workspace.sanitize_dir_name("  ") == ""


def test_legacy_migration_moves_subdirs(tmp_path: Path):
    legacy = tmp_path / "workspaces" / "u1"
    (legacy / "files").mkdir(parents=True)
    (legacy / "files" / "a.txt").write_text("x", encoding="utf-8")
    (legacy / "tmp").mkdir()
    moved = workspace.migrate_legacy_layout(tmp_path, "u1")
    assert moved is True
    assert (legacy / "default" / "files" / "a.txt").read_text(encoding="utf-8") == "x"
    assert (legacy / "default" / "tmp").is_dir()


def test_legacy_migration_is_idempotent(tmp_path: Path):
    legacy = tmp_path / "workspaces" / "u1"
    (legacy / "files").mkdir(parents=True)
    assert workspace.migrate_legacy_layout(tmp_path, "u1") is True
    assert workspace.migrate_legacy_layout(tmp_path, "u1") is False


def test_free_dir_name_suffixes_on_disk_collision(tmp_path: Path):
    user_dir = tmp_path / "workspaces" / "u1"
    (user_dir / "实验一").mkdir(parents=True)
    assert workspace.free_dir_name(user_dir, "实验一", set()) == "实验一-2"
    assert workspace.free_dir_name(user_dir, "实验一", {"实验一-2"}) == "实验一-3"


def test_remove_dir_renames_to_trash_when_locked(tmp_path: Path, monkeypatch):
    target = tmp_path / "proj"
    target.mkdir()
    (target / "a.txt").write_text("x", encoding="utf-8")

    def boom(*_a, **_k):
        raise OSError("locked")

    monkeypatch.setattr(workspace.shutil, "rmtree", boom)
    assert workspace.remove_project_dir(target) is False
    assert not target.exists()
    assert list(tmp_path.glob("proj.trash-*"))
```

> **审查后补的 6 个边界用例**（已落地在 `test_workspace_layout.py`，实现见下方 Step 3）：`test_project_root_rejects_illegal_dir_name`、`test_free_dir_name_rejects_empty_base`、`test_remove_project_dir_missing_target_is_true`、`test_remove_project_dir_trash_names_unique_with_same_timestamp`（用 `monkeypatch.setattr(workspace.time, "time", lambda: 1789101841)` 冻结时间戳）、`test_migrate_legacy_moves_partial_subdirs`、`test_migrate_legacy_resumes_after_partial`。

- [x] **Step 2: 跑测试确认失败**

Run: `cd apps/web/backend && conda run -n synlysagent --no-capture-output python -m pytest tests/test_workspace_layout.py -v`
Expected: FAIL — `AttributeError: module 'app.services.workspace' has no attribute 'project_root'`

- [ ] **Step 3: 实现**

在 `workspace.py` 追加。（**已完成，以下是最终经过审查修复的实现，以此为准**；现有 `workspace_root` 只把内部子目录元组换成常量 `PROJECT_SUBDIRS`，**不要**让它委托 `project_root`——`migrate_legacy_layout` 依赖旧版 `{uid}/files` 的存在性探测，委托后会自我破坏。）

```python
# 文件顶部 import：os / re / shutil / time / pathlib.Path / uuid.uuid4
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_一-鿿-]+")
DEFAULT_PROJECT_DIR = "default"
PROJECT_SUBDIRS = ("files", "output", "tmp")


def sanitize_dir_name(name: str) -> str:
    """把项目名安全化为目录名（去掉路径分隔符与特殊字符）。

    Args:
        name: 用户输入的项目名。

    Returns:
        只含字母/数字/下划线/连字符/中文的目录名；全被过滤时返回空串
        （调用方须对空串做回退或拒绝）。
    """
    return _SAFE_NAME.sub("_", name.strip()).strip("_")


def project_root(data_root: Path, user_id: str, dir_name: str) -> Path:
    """项目根目录（自动创建 files/output/tmp）。

    Args:
        data_root: 数据根目录。
        user_id: 用户 sub。
        dir_name: 项目目录名（须非空且不含路径分隔符）。

    Returns:
        {data_root}/workspaces/{user_id}/{dir_name} 路径。

    Raises:
        ValueError: dir_name 为空、为 . / .. 或含路径分隔符（防止越界写出用户目录）。
    """
    if not dir_name or dir_name in {".", ".."} or "/" in dir_name or "\\" in dir_name:
        raise ValueError(f"非法项目目录名: {dir_name!r}")
    root = data_root / "workspaces" / user_id / dir_name
    for sub in PROJECT_SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def migrate_legacy_layout(data_root: Path, user_id: str) -> bool:
    """把旧版 {uid}/files|output|tmp 逐个子目录迁进 {uid}/default/（幂等）。

    逐个子目录独立判断而非整体早退：只有部分子目录存在、或上次迁移中断时，
    仍能把剩余目录搬过去，不会永久遗弃旧数据。

    Args:
        data_root: 数据根目录。
        user_id: 用户 sub。

    Returns:
        True 表示本次至少搬动了一个子目录，False 表示无可迁移项。

    Raises:
        OSError: 底层文件操作失败（可能已部分迁移，下次调用会续迁剩余部分）。
    """
    user_dir = data_root / "workspaces" / user_id
    target = user_dir / DEFAULT_PROJECT_DIR
    moved = False
    for sub in PROJECT_SUBDIRS:
        src = user_dir / sub
        dst = target / sub
        if not src.is_dir() or dst.exists():
            continue
        target.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
        moved = True
    return moved


def resolve_in_project(root: Path, rel: str) -> Path:
    """把相对路径解析到项目根内（越界抛 ValueError）。

    Args:
        root: 项目根目录。
        rel: 用户给的相对路径。

    Returns:
        绝对路径，保证位于 root 之内。

    Raises:
        ValueError: 路径逃逸出项目根。
    """
    candidate = (root / rel).resolve()
    if candidate != root.resolve() and root.resolve() not in candidate.parents:
        raise ValueError("路径越界")
    return candidate


def free_dir_name(user_dir: Path, base: str, taken: set[str]) -> str:
    """求一个未被占用的目录名（盘上存在或仍被活跃项目引用都算占用）。

    Args:
        user_dir: 该用户的工作区目录（{data_root}/workspaces/{user_id}）。
        base: sanitize 后的基础目录名（须非空）。
        taken: 仍被活跃项目引用的目录名集合。

    Returns:
        base 本身，或 base-2 / base-3 …

    Raises:
        ValueError: base 为空（调用方须先回退占位名或拒绝该请求）。
    """
    if not base:
        raise ValueError("base 不能为空（调用方须先回退占位名或拒绝该请求）")
    name = base
    i = 1
    while name in taken or (user_dir / name).exists():
        i += 1
        name = f"{base}-{i}"
    return name


def remove_project_dir(target: Path) -> bool:
    """删除项目目录；失败则改名为 {name}.trash-{时间戳}-{uuid 后缀} 释放目录名并保住数据。

    Args:
        target: 项目根目录。

    Returns:
        True 表示已彻底删除，False 表示退化为 trash 改名。

    Raises:
        OSError: 删除与兜底改名均失败（此时目录既未删除、名字也未释放）。
    """
    if not target.exists():
        return True
    try:
        shutil.rmtree(target)
        return True
    except OSError:
        pass
    # 时间戳 + uuid 后缀：Windows 上 time_ns() 实际粒度约 15ms，仅靠时间戳仍可能撞名
    trash = target.with_name(f"{target.name}.trash-{int(time.time())}-{uuid4().hex[:8]}")
    try:
        target.rename(trash)
    except OSError as exc:
        raise OSError(f"删除项目目录失败且无法改名为 trash：{target}") from exc
    return False
```

> **审查修复记录**（初版有 5 处缺陷，已在提交 `308a185` / `b891364` 修掉，补了 6 个边界用例）：① `project_root` 不校验目录名会越界写出用户目录；② `free_dir_name` 空 base 静默返回 `""`/`"-2"`；③ trash 改名用秒级时间戳会撞名抛 `FileExistsError`，反而破坏「名字必被释放」的契约；④ `migrate_legacy_layout` 用 `target.exists()` 整体早退，只有 `output`/`tmp` 或迁移中断时会**永久遗弃旧数据**；⑤ `workspace_root` 硬编码子目录元组。

> **为什么这样设计**：若删除只删记录、留目录，之后重建同名项目会命中 `mkdir(exist_ok=True)` 而**静默继承上一个项目的残留文件**；反之若强删目录，Windows 上文件被占用会让 `rmtree` 抛错、同样留下孤儿目录。`free_dir_name` 保证创建永不因重名失败，`remove_project_dir` 保证删除失败时目录名也能被释放。

- [x] **Step 4: 跑测试确认通过**

Run: `cd apps/web/backend && conda run -n synlysagent --no-capture-output python -m pytest tests/test_workspace_layout.py -v`
Expected: PASS（12 passed）；全量后端 `pytest -q` → 105 passed / 16 skipped

- [x] **Step 5: Commit**

```bash
git add apps/web/backend/app/services/workspace.py apps/web/backend/tests/test_workspace_layout.py
git commit -m "feat(web): 工作区多项目目录布局与旧数据迁移"
```

> **本任务已完成**：`521a017`（初版）+ `308a185` / `b891364`（审查修复）。两轮审查（规格 + 代码质量 + 修复复验）均通过。

---

## Task C2: ProjectRepo

**Files:** Modify `apps/web/backend/app/db/repos.py`；Test `apps/web/backend/tests/test_repos.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
async def test_project_create_and_list(store):
    from app.db.repos import ProjectRepo
    repo = ProjectRepo(store)
    p = await repo.create(user_id="u1", name="我的项目", dir_name="我的项目")
    assert p["name"] == "我的项目" and p["archived"] is False
    assert [x["_id"] for x in await repo.list_for_user("u1")] == [p["_id"]]


async def test_project_delete_removes_record(store):
    from app.db.repos import ProjectRepo
    repo = ProjectRepo(store)
    p = await repo.create(user_id="u1", name="a", dir_name="a")
    assert await repo.used_dir_names("u1") == {"a"}
    assert await repo.delete(p["_id"]) is True
    assert await repo.list_for_user("u1") == []
```

（`store` fixture 复用 `tests/conftest.py` 现有实现。）

- [ ] **Step 2: 跑测试确认失败** — `AttributeError: cannot import name 'ProjectRepo'`

- [ ] **Step 3: 实现**

```python
class ProjectRepo(BaseRepo):
    """用户项目（目录名在用户内唯一）。"""

    collection = "projects"

    async def list_for_user(self, user_id: str) -> list[dict]:
        """列出用户的未归档项目（updated_at 倒序）。"""
        docs = await self._store.list(self.collection, filters={"user_id": user_id})
        return sorted(
            (d for d in docs if not d.get("archived")),
            key=lambda d: d.get("updated_at", 0), reverse=True,
        )

    async def create(self, *, user_id: str, name: str, dir_name: str) -> dict:
        """新建项目（目录名唯一性由 ProjectService 保证，此处不校验）。"""
        now = time.time()
        return await self._store.insert(self.collection, {
            "_id": uuid.uuid4().hex, "user_id": user_id, "name": name,
            "dir_name": dir_name, "archived": False,
            "created_at": now, "updated_at": now,
        })

    async def used_dir_names(self, user_id: str) -> set[str]:
        """该用户仍被占用的目录名（去重时用）。"""
        docs = await self._store.list(self.collection, filters={"user_id": user_id})
        return {d["dir_name"] for d in docs}

    async def delete(self, project_id: str) -> bool:
        """删除项目记录。"""
        return await self._store.delete(self.collection, project_id)

    async def rename(self, project_id: str, *, name: str, dir_name: str) -> dict | None:
        """改名（同步更新目录名）。"""
        return await self._store.update(self.collection, project_id, {
            "name": name, "dir_name": dir_name, "updated_at": time.time()})
```

- [ ] **Step 4: 跑测试确认通过** — `pytest tests/test_repos.py -v`，PASS

- [ ] **Step 5: Commit** — `feat(web): 项目仓储`

---

## Task C3: project_service（编排：迁移 + 建目录 + 目录树）

**Files:** Create `apps/web/backend/app/services/project_service.py`；Test `apps/web/backend/tests/test_project_service.py`

- [ ] **Step 1: 写失败测试**

```python
async def test_list_ensures_default_project_on_legacy(tmp_path, store):
    (tmp_path / "workspaces" / "u1" / "files").mkdir(parents=True)
    (tmp_path / "workspaces" / "u1" / "files" / "a.txt").write_text("x", encoding="utf-8")
    svc = ProjectService(store, tmp_path)
    projects = await svc.list_projects("u1")
    assert [p["dir_name"] for p in projects] == ["default"]
    assert (tmp_path / "workspaces" / "u1" / "default" / "files" / "a.txt").exists()


async def test_list_returns_empty_for_brand_new_user(tmp_path, store):
    svc = ProjectService(store, tmp_path)
    assert await svc.list_projects("u2") == []


async def test_tree_lists_one_level(tmp_path, store):
    svc = ProjectService(store, tmp_path)
    p = await svc.create_project("u1", "proj")
    root = svc.root_for(p)
    (root / "files" / "a.txt").write_text("x", encoding="utf-8")
    (root / "files" / "sub").mkdir()
    entries = await svc.list_dir("u1", p["_id"], "files")
    names = {e["name"]: e["is_dir"] for e in entries}
    assert names == {"a.txt": False, "sub": True}


async def test_tree_rejects_escape(tmp_path, store):
    svc = ProjectService(store, tmp_path)
    p = await svc.create_project("u1", "proj")
    with pytest.raises(ValueError):
        await svc.list_dir("u1", p["_id"], "../../../etc")


async def test_create_two_projects_same_name_gets_distinct_dirs(tmp_path, store):
    svc = ProjectService(store, tmp_path)
    a = await svc.create_project("u1", "实验一")
    b = await svc.create_project("u1", "实验一")
    assert a["dir_name"] == "实验一"
    assert b["dir_name"] == "实验一-2"


async def test_delete_frees_name_for_recreate(tmp_path, store):
    svc = ProjectService(store, tmp_path)
    a = await svc.create_project("u1", "实验一")
    assert await svc.delete_project("u1", a["_id"]) is True
    b = await svc.create_project("u1", "实验一")
    assert b["dir_name"] == "实验一"
    assert (tmp_path / "workspaces" / "u1" / "实验一").is_dir()
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

```python
"""项目服务：项目 CRUD 编排、目录树懒加载、旧数据迁移。"""
from __future__ import annotations

from pathlib import Path

from ..db.repos import ProjectRepo
from . import workspace


class ProjectService:
    """项目与其磁盘目录的编排层。"""

    def __init__(self, store, data_root: Path) -> None:
        self._repo = ProjectRepo(store)
        self._data_root = data_root

    async def list_projects(self, user_id: str) -> list[dict]:
        """列出项目（首次访问时执行旧布局迁移并补种默认项目）。"""
        migrated = workspace.migrate_legacy_layout(self._data_root, user_id)
        if migrated:
            await self._repo.create(
                user_id=user_id, name="默认项目",
                dir_name=workspace.DEFAULT_PROJECT_DIR,
            )
        return await self._repo.list_for_user(user_id)

    async def create_project(self, user_id: str, name: str) -> dict:
        """新建项目并创建其目录（目录名重复时自动加后缀，不会因重名失败）。"""
        base = workspace.sanitize_dir_name(name)
        if not base:
            raise ValueError("项目名不合法")
        user_dir = self._data_root / "workspaces" / user_id
        taken = await self._repo.used_dir_names(user_id)
        dir_name = workspace.free_dir_name(user_dir, base, taken)
        project = await self._repo.create(user_id=user_id, name=name.strip(), dir_name=dir_name)
        workspace.project_root(self._data_root, user_id, dir_name)
        return project

    async def delete_project(self, user_id: str, project_id: str) -> bool:
        """删除项目记录与磁盘目录（目录删不掉时改名 trash 释放名字）。

        Returns:
            True 表示记录与目录都已彻底删除。
        """
        project = await self.get(user_id, project_id)
        if project is None:
            return False
        removed = workspace.remove_project_dir(self.root_for(project))
        await self._repo.delete(project_id)
        return removed

    async def get(self, user_id: str, project_id: str) -> dict | None:
        """取项目（校验归属）。"""
        doc = await self._repo.get(project_id)
        return doc if doc and doc["user_id"] == user_id else None

    def root_for(self, project: dict) -> Path:
        """项目对应的磁盘根目录。"""
        return workspace.project_root(
            self._data_root, project["user_id"], project["dir_name"])

    async def list_dir(self, user_id: str, project_id: str, rel: str = "") -> list[dict]:
        """列出项目内某目录的一级条目（懒加载用）。"""
        project = await self.get(user_id, project_id)
        if project is None:
            raise ValueError("项目不存在")
        root = self.root_for(project)
        target = workspace.resolve_in_project(root, rel)
        if not target.is_dir():
            raise ValueError("不是目录")
        out = []
        for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
            stat = entry.stat()
            out.append({
                "name": entry.name,
                "path": str(entry.relative_to(root)).replace("\\", "/"),
                "is_dir": entry.is_dir(),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            })
        return out
```

- [ ] **Step 4: 跑测试确认通过**

- [ ] **Step 5: Commit** — `feat(web): 项目服务（迁移/建目录/目录树）`

---

## Task C4: projects API

**Files:** Create `apps/web/backend/app/api/projects_api.py`；Modify `app/main.py`；Test `apps/web/backend/tests/test_projects_api.py`

- [ ] **Step 1: 写失败测试**

```python
async def test_list_projects_returns_empty(client, auth_headers):
    r = await client.get("/api/v1/projects", headers=auth_headers)
    assert r.status_code == 200 and r.json() == []


async def test_create_project_then_tree(client, auth_headers):
    r = await client.post("/api/v1/projects", json={"name": "实验一"}, headers=auth_headers)
    assert r.status_code == 201
    pid = r.json()["_id"]
    assert r.json()["dir_name"] == "实验一"

    r2 = await client.get(f"/api/v1/projects/{pid}/tree", headers=auth_headers)
    assert r2.status_code == 200
    assert {e["name"] for e in r2.json()} == {"files", "output", "tmp"}


async def test_create_duplicate_name_succeeds_with_distinct_dir(client, auth_headers):
    await client.post("/api/v1/projects", json={"name": "x"}, headers=auth_headers)
    r = await client.post("/api/v1/projects", json={"name": "x"}, headers=auth_headers)
    assert r.status_code == 201 and r.json()["dir_name"] == "x-2"


async def test_create_project_rejects_empty_name(client, auth_headers):
    r = await client.post("/api/v1/projects", json={"name": "   "}, headers=auth_headers)
    assert r.status_code == 422


async def test_delete_project_removes_it(client, auth_headers):
    pid = (await client.post("/api/v1/projects", json={"name": "x"},
                             headers=auth_headers)).json()["_id"]
    assert (await client.delete(f"/api/v1/projects/{pid}", headers=auth_headers)).status_code == 200
    assert (await client.get("/api/v1/projects", headers=auth_headers)).json() == []
```

- [ ] **Step 2: 跑测试确认失败** — 404

- [ ] **Step 3: 实现**

```python
"""项目 API：列表/新建/删除 + 目录树懒加载。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.api.deps import get_current_user

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


class ProjectCreateBody(BaseModel):
    """新建项目请求体。"""

    name: str


class ProjectRenameBody(BaseModel):
    """改名请求体。"""

    name: str


@router.get("")
async def list_projects(request: Request, user=Depends(get_current_user)):
    """列出当前用户的项目（首次访问会补种默认项目）。"""
    return await request.app.state.project_service.list_projects(user["sub"])


@router.post("", status_code=201)
async def create_project(request: Request, body: ProjectCreateBody, user=Depends(get_current_user)):
    """新建项目（重名自动加后缀，不失败）。"""
    try:
        return await request.app.state.project_service.create_project(user["sub"], body.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{pid}")
async def delete_project(request: Request, pid: str, user=Depends(get_current_user)):
    """删除项目（记录 + 目录）。"""
    ok = await request.app.state.project_service.delete_project(user["sub"], pid)
    if not ok:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"ok": True}


@router.get("/{pid}/tree")
async def list_tree(request: Request, pid: str, path: str = Query(""), user=Depends(get_current_user)):
    """列出项目内某目录的一级条目（path 为空表示根）。"""
    try:
        return await request.app.state.project_service.list_dir(user["sub"], pid, path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
```

`main.py` 里建单例并挂载：

```python
app.state.project_service = ProjectService(store, settings.data_root)
app.include_router(projects_router)
```

- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: Commit** — `feat(web): 项目 API 与目录树接口`

---

## Task C5: 会话绑定项目 + agent_service 用项目目录

**Files:** Modify `app/api/sessions_api.py`、`app/services/agent_service.py`；Test `tests/test_chat_api.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
async def test_session_binds_project_and_uses_project_workspace(client, auth_headers, tmp_path):
    pid = (await client.post("/api/v1/projects", json={"name": "p1"}, headers=auth_headers)).json()["_id"]
    r = await client.post("/api/v1/sessions", json={"assistant_id": "asst-research", "project_id": pid},
                          headers=auth_headers)
    assert r.json()["project_id"] == pid
```

- [ ] **Step 2: 跑测试确认失败** — `project_id` KeyError

- [ ] **Step 3: 实现**

`sessions_api.py`：`SessionCreateBody` 增 `project_id: str | None = None`；`create_session` 落库时带上 `"project_id": body.project_id`；发消息时解析：

```python
project_id = session.get("project_id")
project = await request.app.state.project_service.get(user["sub"], project_id) if project_id else None
if project is None:
    projects = await request.app.state.project_service.list_projects(user["sub"])
    project = projects[0] if projects else await request.app.state.project_service.create_project(
        user["sub"], "默认项目")
workspace_root = request.app.state.project_service.root_for(project)
```

`agent_service.chat(...)` 增加 `workspace_root` 形参（替换第 177 行自身拼路径的逻辑），保持默认参数以兼容既有调用点。

- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: Commit** — `feat(web): 会话绑定项目并使用项目工作区目录`

---

## Task C6: 文件 API 项目作用域化

**Files:** Modify `app/api/files_api.py`；Test `tests/test_files_api.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
async def test_upload_scoped_to_project(client, auth_headers):
    pid = (await client.post("/api/v1/projects", json={"name": "p"}, headers=auth_headers)).json()["_id"]
    files = {"files": ("a.txt", b"hello", "text/plain")}
    r = await client.post("/api/v1/projects/%s/files" % pid, files=files, headers=auth_headers)
    assert r.status_code == 201
    listing = (await client.get("/api/v1/projects/%s/tree" % pid, headers=auth_headers)).json()
    assert "files" in {e["name"] for e in listing}


async def test_upload_rejects_executable(client, auth_headers):
    pid = (await client.post("/api/v1/projects", json={"name": "p"}, headers=auth_headers)).json()["_id"]
    files = {"files": ("x.exe", b"MZ", "application/octet-stream")}
    r = await client.post("/api/v1/projects/%s/files" % pid, files=files, headers=auth_headers)
    assert r.status_code == 422
```

- [ ] **Step 2: 跑测试确认失败** — 404

- [ ] **Step 3: 实现**

把 `files_api.py` 的落盘根从 `workspace.workspace_root(...)` 换成 `project_service.root_for(project)` 下的 `files/`；新增项目作用域路由 `POST/GET /api/v1/projects/{pid}/files`，**保留旧 `/api/v1/files`**（内部解析为当前默认项目）以免既有前端报错。文档 `stored_path` 语义不变（相对 `files/`）。

- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: Commit** — `feat(web): 文件上传按项目作用域化`

---

## Task C7: 前端项目选择器 + 侧栏/右栏接线

**Files:**
- Create `apps/web/frontend/src/stores/projects.ts`、`components/chat/ProjectPicker.tsx`、`components/rightbar/FileTree.tsx`、`components/rightbar/WorkspacePanel.tsx`
- Modify `components/chat/Composer.tsx`、`components/rightbar/Rightbar.tsx`、`stores/sessions.ts`、`types.ts`

- [ ] **Step 1: types + store**

`types.ts` 追加：

```ts
export interface Project {
  _id: string
  user_id: string
  name: string
  dir_name: string
  archived: boolean
  created_at: number
  updated_at: number
}

export interface TreeEntry {
  name: string
  path: string
  is_dir: boolean
  size: number
  mtime: number
}
```

`stores/projects.ts`：`projects / currentId / load() / create(name) / setCurrent(id)`，`load` 后无当前项目时取列表首个。

- [ ] **Step 2: ProjectPicker**（输入框下方 chip，jiuwen 「选择项目目录」形态）

```tsx
<button className="flex items-center gap-1.5 text-[12.5px] text-[var(--sa-alias-label-tertiary)] hover:text-[var(--sa-alias-label-primary)]">
  <FolderIcon /> {current?.name ?? '选择项目'} <ChevronDownIcon />
</button>
```

点开菜单：项目列表（选中打勾）+「新建项目」（点开后 `window.prompt` 取名字，V1 不做独立弹窗）。

- [ ] **Step 3: FileTree**（照 DSH `FilesBody.tsx`：嵌套 DOM + 每层懒加载 + 目录优先排序）

```tsx
/** 单层目录（懒加载：展开时才请求）。 */
function Level({ projectId, path }: { projectId: string; path: string }) {
  const [entries, setEntries] = useState<TreeEntry[] | null>(null)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  useEffect(() => {
    void api<TreeEntry[]>(`/api/v1/projects/${projectId}/tree?path=${encodeURIComponent(path)}`)
      .then(setEntries).catch(() => setEntries([]))
  }, [projectId, path])
  ...
}
```

- [ ] **Step 4: WorkspacePanel** 替换 `Rightbar` 里的 `FilesPanel`：顶部上传区（复用现有上传逻辑，改投新路由）+ `FileTree`。
- [ ] **Step 5: 手工验证**：起后端 + `npm run build`，playwright 有头模式：新建项目 → 右栏根目录出现 `files/output/tmp` → 拖文件上传 → 树里可见。

Run: `cd apps/web/frontend && npm run build && npm run lint`
Expected: 构建通过；lint 无新增警告

- [ ] **Step 6: Commit** — `feat(frontend): 项目选择器与工作区目录树`

---

# Phase B：技能框架 + 默认系统提示词

## Task B1: 提示词分段与组合器（harness）

**Files:** Create `packages/synlys-harness/src/synlys_harness/prompts.py`；Test `packages/synlys-harness/tests/test_prompts.py`

- [ ] **Step 1: 写失败测试**

```python
from synlys_harness.prompts import build_system_prompt, render_skill_index

def test_persona_appended_after_platform_sections():
    out = build_system_prompt(persona="你是高分子专家。", workspace=None, skills=[])
    assert out.index("核心原则") < out.index("你是高分子专家。")

def test_workspace_placeholder_substituted(tmp_path):
    out = build_system_prompt(persona="", workspace=tmp_path, skills=[])
    assert str(tmp_path) in out and "{{workspace}}" not in out

def test_skill_index_rendered_only_when_present():
    out = build_system_prompt(persona="", workspace=None, skills=[("pdf-extraction", "抽取 PDF 文本")])
    assert "`pdf-extraction`：抽取 PDF 文本" in out
    assert "skill.read" in out
    assert build_system_prompt(persona="", workspace=None, skills=[]).count("# 技能") == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/synlys-harness && conda run -n synlysagent --no-capture-output python -m pytest tests/test_prompts.py -v`

- [ ] **Step 3: 实现**

```python
"""系统提示词分段组合：平台默认段（按 priority 升序）+ 技能索引 + 专家 persona 追加。"""
from __future__ import annotations

import re
from pathlib import Path

# 平台默认段：(priority, 文本)。priority 参照 jiuwen PromptPriority。
IDENTITY_PRIORITY = 10
TASK_PRIORITY = 21
SKILLS_PRIORITY = 40
WORKSPACE_PRIORITY = 70

SOUL = """# 灵魂

## 身份定位
你不是一个聊天机器人。你是一个科研智能体，通过工具真实地把事情做完。

## 核心原则
- 真正有帮助，而不是表演有帮助。省掉废话，直接帮。
- 先自己查，再问人。读文件、看上下文、跑一下，搞不定再问。
- 报数字要有出处。引用数据/文件时说明来自哪个文件。
- 做不到就说做不到，不要编。

## 风格
需要简洁就简洁，需要详尽就详尽。专业、靠谱。"""

AGENT = """# 工作方式

## 会话启动
先搞清楚用户想要什么，再动手。涉及工作区文件时，先列目录看看有什么。

## 工具使用
- 需要计算、画图、处理数据 → 用 `python.run`（工作目录是 `tmp/`）。
- 读写工作区文件 → 用 `file.read` / `file.write` / `file.list`。
- 工具报错就按错误信息调整，不要反复重试同样的调用。

## 任务管理
多步任务先在心里列清步骤再执行；做完给出结论与产物路径（如 `output/xxx.png`）。"""

WORKSPACE_SECTION = """# 工作区

你的工作目录是 `{{workspace}}`。
- `files/` 用户上传的原始文件
- `output/` 你产出的最终结果（图表、报告等）
- `tmp/` 临时中间文件，`python.run` 的当前目录

引用产物时用相对工作区根的路径。"""

SKILLS_HEADER = """# 技能

选择与任务最相关的技能，使用技能前，调用 `skill.read` 获取该技能的完整 `SKILL.md`。

当前可用技能：

"""

_PLACEHOLDER = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")


def render_skill_index(skills: list[tuple[str, str]]) -> str:
    """渲染技能索引段（仅 name + description）。

    Args:
        skills: (技能名, 描述) 列表。

    Returns:
        技能索引文本；空列表返回空串。
    """
    if not skills:
        return ""
    lines = [f"{i}. `{name}`：{desc}" for i, (name, desc) in enumerate(skills, 1)]
    return SKILLS_HEADER + "\n".join(lines)


def build_system_prompt(
    *,
    persona: str,
    workspace: Path | None,
    skills: list[tuple[str, str]],
) -> str:
    """拼出最终 system prompt。

    Args:
        persona: 专家自己的 system_prompt（追加在平台默认段之后）。
        workspace: 工作区根目录（None 时省略工作区段）。
        skills: 该会话启用的技能索引。

    Returns:
        各段按 priority 升序、以空行连接，末尾追加 persona。
    """
    params = {"workspace": str(workspace) if workspace else ""}
    sections: list[tuple[int, str]] = [
        (IDENTITY_PRIORITY, SOUL),
        (TASK_PRIORITY, AGENT),
    ]
    if workspace is not None:
        sections.append((WORKSPACE_PRIORITY, WORKSPACE_SECTION))
    index = render_skill_index(skills)
    if index:
        sections.append((SKILLS_PRIORITY, index))

    rendered = [
        _PLACEHOLDER.sub(lambda m: params.get(m.group(1), m.group(0)), text).strip()
        for _, text in sorted(sections, key=lambda item: item[0])
    ]
    rendered = [t for t in rendered if t]
    if persona.strip():
        rendered.append(persona.strip())
    return "\n\n".join(rendered)
```

- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: Commit** — `feat(harness): 系统提示词分段组合器与技能索引`

---

## Task B2: skill.list / skill.read 工具（harness）

**Files:** Modify `packages/synlys-harness/src/synlys_harness/tools/builtin.py`；Test `tests/test_builtin.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
async def test_skill_list_and_read(tmp_path):
    ctx = ToolContext(user_id="u", run_id="r", workspace_root=tmp_path,
                      extra={"skills": {"pdf-extraction": "# PDF 抽取\n\n步骤..."}})
    listed = await skill_list(ctx, {})
    assert listed.ok and "pdf-extraction" in listed.content
    read = await skill_read(ctx, {"name": "pdf-extraction"})
    assert read.ok and read.data["content"].startswith("# PDF 抽取")


async def test_skill_read_unknown_name(tmp_path):
    ctx = ToolContext(user_id="u", run_id="r", workspace_root=tmp_path, extra={"skills": {}})
    res = await skill_read(ctx, {"name": "nope"})
    assert res.ok is False and "nope" in (res.error or "")
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**（追加到 `builtin.py`，并在 `tools/__init__.py` 导出）

```python
@tool(
    name="skill.list",
    description="列出本会话可用技能（名称 + 用途）。不确定该用哪个技能时先调用它。",
    parameters={"type": "object", "properties": {}, "required": []},
)
async def skill_list(ctx: ToolContext, args: dict) -> ToolResult:
    """列出可用技能。"""
    skills: dict[str, str] = ctx.extra.get("skills") or {}
    listing = ctx.extra.get("skill_meta") or {}
    if not skills:
        return ToolResult(ok=True, content="当前没有可用技能。")
    lines = [f"- {name}：{listing.get(name, '')}" for name in skills]
    return ToolResult(ok=True, content="可用技能：\n" + "\n".join(lines))


@tool(
    name="skill.read",
    description="读取某个技能的完整 SKILL.md 正文（含工作流与输出要求）。",
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "技能名"}},
        "required": ["name"],
    },
)
async def skill_read(ctx: ToolContext, args: dict) -> ToolResult:
    """读取技能正文。"""
    name = str(args.get("name", "")).strip()
    skills: dict[str, str] = ctx.extra.get("skills") or {}
    content = skills.get(name)
    if content is None:
        return ToolResult(ok=False, error=f"技能不存在：{name}")
    return ToolResult(ok=True, content=content, data={"name": name, "content": content})
```

- [ ] **Step 4: 跑测试确认通过** — `pytest tests/test_builtin.py -v`

- [ ] **Step 5: Commit** — `feat(harness): skill.list / skill.read 渐进披露工具`

---

## Task B3: 技能文件服务（扫描 / 解析 / 写入 SKILL.md）

**Files:** Create `apps/web/backend/app/services/skill_service.py`；Create `packages/synlys-harness/src/synlys_harness/resources/skills/{data-analysis,pdf-extraction}/SKILL.md`；Test `apps/web/backend/tests/test_skill_service.py`

技能是**磁盘文件，不入库**（依据见上文「技能存储调研」）。布局：

```
{data_root}/skills/<name>/SKILL.md                      # 后台创建/编辑
{data_root}/skills/<name>/scripts|references|assets/    # 可选，V1 不做编辑 UI
```

内置种子随包发布在 `packages/synlys-harness/src/synlys_harness/resources/skills/`，启动时**拷贝**到 `{data_root}/skills/`（已存在则跳过，幂等，不覆盖用户改动）。需要在 harness 的 `pyproject.toml` 里带 shim 声明 package-data，确保 `resources/skills/**` 随包安装。

- [ ] **Step 1: 写失败测试**

```python
# apps/web/backend/tests/test_skill_service.py
def _seed_one(root, name="data-analysis", desc="数据分析"):
    d = root / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\ntags:\n  - 统计\n---\n\n# 目标\n分析数据",
        encoding="utf-8")


def test_scan_reads_frontmatter(tmp_path):
    _seed_one(tmp_path)
    skills = SkillService(tmp_path).list_skills()
    assert skills[0]["name"] == "data-analysis"
    assert skills[0]["description"] == "数据分析"
    assert skills[0]["tags"] == ["统计"]
    assert skills[0]["builtin"] is True


def test_write_then_read_roundtrip(tmp_path):
    svc = SkillService(tmp_path)
    svc.write_skill(name="my-skill", description="用途", content="# 目标\n做点事")
    body = svc.read_body("my-skill")
    assert body.startswith("# 目标")
    assert not body.startswith("---")          # 正文不含 frontmatter


def test_write_rejects_bad_name(tmp_path):
    with pytest.raises(ValueError):
        SkillService(tmp_path).write_skill(name="Bad Name!", description="d", content="x")


def test_read_unknown_skill_returns_none(tmp_path):
    assert SkillService(tmp_path).read_body("nope") is None


def test_scan_skips_broken_skill(tmp_path):
    (tmp_path / "skills" / "broken").mkdir(parents=True)
    (tmp_path / "skills" / "broken" / "SKILL.md").write_text("没有 frontmatter", encoding="utf-8")
    assert SkillService(tmp_path).list_skills() == []


def test_delete_builtin_rejected(tmp_path):
    _seed_one(tmp_path)
    with pytest.raises(ValueError):
        SkillService(tmp_path).delete_skill("data-analysis")


def test_seed_builtins_is_idempotent(tmp_path):
    svc = SkillService(tmp_path)
    svc.seed_builtins()
    md = tmp_path / "skills" / "data-analysis" / "SKILL.md"
    assert md.is_file()
    first = md.read_text(encoding="utf-8")
    md.write_text(first + "\n用户追加", encoding="utf-8")
    svc.seed_builtins()                          # 二次播种不覆盖
    assert md.read_text(encoding="utf-8").endswith("用户追加")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/web/backend && conda run -n synlysagent --no-capture-output python -m pytest tests/test_skill_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.skill_service'`

- [ ] **Step 3: 实现**

```python
"""技能：磁盘 SKILL.md 的扫描/解析/写入（不入库）。

字段与正文骨架遵循 jiuwen `skill-spec.md`：frontmatter 必填
`name`（= 目录名，kebab-case）/ `description`（做什么 + 何时用），可选
`version` / `author` / `tags` / `allowed_tools`；正文骨架
`# 标题 → ## 目标 → ## 工作流 → ## 决策规则 → ## 输出要求`。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import synlys_harness
import yaml

NAME_OK = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
BUILTIN_SKILL_NAMES = frozenset({"data-analysis", "pdf-extraction"})
RESOURCE_SKILLS = Path(synlys_harness.__file__).resolve().parent / "resources" / "skills"


def parse_skill_md(text: str) -> dict:
    """解析 SKILL.md 全文。

    Args:
        text: SKILL.md 内容。

    Returns:
        含 name/description/version/author/tags/allowed_tools/content 的字典。

    Raises:
        ValueError: frontmatter 缺失，或缺 name/description，或 name 非 kebab-case。
    """
    match = FRONTMATTER.match(text)
    if not match:
        raise ValueError("缺少 frontmatter")
    meta = yaml.safe_load(match.group(1)) or {}
    name = str(meta.get("name") or "").strip()
    description = str(meta.get("description") or "").strip()
    if not NAME_OK.match(name):
        raise ValueError(f"技能名必须是 kebab-case: {name!r}")
    if not description:
        raise ValueError("description 必填")
    return {
        "name": name,
        "description": description,
        "version": str(meta.get("version") or "1.0"),
        "author": str(meta.get("author") or ""),
        "tags": [str(t) for t in (meta.get("tags") or [])],
        "allowed_tools": [str(t) for t in (meta.get("allowed_tools") or [])],
        "content": match.group(2).strip(),
    }


def render_skill_md(skill: dict) -> str:
    """把技能字段渲染回 SKILL.md 文本（落盘/导出共用）。"""
    front = yaml.safe_dump(
        {
            "name": skill["name"],
            "description": skill["description"],
            "version": skill.get("version") or "1.0",
            "author": skill.get("author") or "",
            "tags": skill.get("tags") or [],
            "allowed_tools": skill.get("allowed_tools") or [],
        },
        allow_unicode=True, sort_keys=False,
    ).strip()
    return f"---\n{front}\n---\n\n{skill['content'].strip()}\n"


class SkillService:
    """{data_root}/skills 下的技能读写与扫描。"""

    def __init__(self, data_root: Path) -> None:
        """保存数据根。

        Args:
            data_root: 应用数据根目录。
        """
        self._data_root = data_root

    @property
    def skills_dir(self) -> Path:
        """技能根目录（自动创建）。"""
        d = self._data_root / "skills"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def list_skills(self) -> list[dict]:
        """扫描全部技能（目录名排序；解析失败的目录跳过，不影响其余）。"""
        out: list[dict] = []
        for entry in sorted(self.skills_dir.iterdir()):
            md = entry / "SKILL.md"
            if not md.is_file():
                continue
            try:
                skill = parse_skill_md(md.read_text(encoding="utf-8"))
            except (ValueError, yaml.YAMLError):
                continue
            skill["builtin"] = skill["name"] in BUILTIN_SKILL_NAMES
            out.append(skill)
        return out

    def read_body(self, name: str) -> str | None:
        """读技能正文（不存在或不可解析返回 None）。"""
        md = self.skills_dir / name / "SKILL.md"
        if not md.is_file():
            return None
        try:
            return parse_skill_md(md.read_text(encoding="utf-8"))["content"]
        except (ValueError, yaml.YAMLError):
            return None

    def write_skill(self, *, name: str, description: str, content: str,
                    version: str = "1.0", author: str = "",
                    tags: list[str] | None = None,
                    allowed_tools: list[str] | None = None) -> dict:
        """写入（新建或覆盖）一个技能目录。

        Raises:
            ValueError: 技能名不是 kebab-case。
        """
        if not NAME_OK.match(name):
            raise ValueError(f"技能名必须是 kebab-case: {name!r}")
        skill = {"name": name, "description": description, "content": content,
                 "version": version, "author": author,
                 "tags": tags or [], "allowed_tools": allowed_tools or []}
        target = self.skills_dir / name
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(render_skill_md(skill), encoding="utf-8")
        return {**skill, "builtin": name in BUILTIN_SKILL_NAMES}

    def delete_skill(self, name: str) -> bool:
        """删除技能目录。

        Raises:
            ValueError: 内置技能不可删。
        """
        target = self.skills_dir / name
        if not target.is_dir():
            return False
        if name in BUILTIN_SKILL_NAMES:
            raise ValueError("内置技能不可删除")
        shutil.rmtree(target)
        return True

    def seed_builtins(self) -> None:
        """把随包发布的内置技能拷到用户技能目录（幂等，不覆盖已有目录）。"""
        if not RESOURCE_SKILLS.is_dir():
            return
        for src in RESOURCE_SKILLS.iterdir():
            dst = self.skills_dir / src.name
            if not dst.exists():
                shutil.copytree(src, dst)
```

内置种子正文（两份，骨架照 jiuwen）：

```markdown
---
name: data-analysis
description: 对工作区里的数据文件做统计分析并出图（CSV/Excel/JSON）。用户说"分析数据""画个图""算一下"时使用。
version: "1.0"
author: SynlysAgent
tags:
  - 统计
  - 可视化
---

# 数据分析

## 目标
把 `files/` 里的数据文件变成结论与图表，产物落在 `output/`。

## 工作流
1. `file.list` 看 `files/` 下有哪些数据文件
2. `python.run` 读入（pandas），先打印形状 / dtype / 缺失值
3. 按需求做统计或作图，图存 `output/`
4. 回复给出结论 + 产物的工作区相对路径

## 决策规则
- 找不到数据文件时先问用户，不要凭空生成数据
- 画图中文标签要显式指定字体，否则乱码

## 输出要求
结论先行、附关键数字；产物给 `output/xxx.png` 路径
```

`pdf-extraction/SKILL.md` 同构（description 写清触发场景，工作流写 `python.run` + pypdf/pdfplumber）。

> **注意**：`name` 必须等于目录名（jiuwen `skill-spec.md` 的硬约束），扫描时以目录名为准。frontmatter **不声明 `tools`**（jiuwen 明确禁止，工具权限由系统分配）。

- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: Commit** — `feat(web): 技能文件服务（SKILL.md 扫描/解析/写入）`

---

## Task B4: skills API（按技能名，读写磁盘）

**Files:** Create `apps/web/backend/app/api/skills_api.py`；Modify `app/main.py`（挂路由 + `app.state.skill_service` + 启动时 `seed_builtins()`）；Test `apps/web/backend/tests/test_skills_api.py`

技能是全局的（admin 管理，所有用户可用），路由用**技能名**而非 id。

- [ ] **Step 1: 写失败测试**

```python
async def test_list_skills_includes_seeds(client, auth_headers):
    names = {s["name"] for s in (await client.get("/api/v1/skills", headers=auth_headers)).json()}
    assert {"data-analysis", "pdf-extraction"} <= names


async def test_create_skill_requires_admin(client, auth_headers):
    r = await client.post("/api/v1/skills",
                          json={"name": "my-skill", "description": "d", "content": "# 目标"},
                          headers=auth_headers)
    assert r.status_code == 403


async def test_admin_creates_then_reads_export(client, admin_headers):
    r = await client.post("/api/v1/skills",
                          json={"name": "my-skill", "description": "用途", "content": "# 目标\n做点事"},
                          headers=admin_headers)
    assert r.status_code == 201
    exported = await client.get("/api/v1/skills/my-skill/export", headers=admin_headers)
    assert exported.status_code == 200
    assert "name: my-skill" in exported.text and "# 目标" in exported.text


async def test_import_skill_md(client, admin_headers):
    r = await client.post("/api/v1/skills/import",
                          json={"text": "---\nname: imported\ndescription: 导入的\n---\n# 目标\n正文"},
                          headers=admin_headers)
    assert r.status_code == 201 and r.json()["name"] == "imported"


async def test_import_bad_text_422(client, admin_headers):
    r = await client.post("/api/v1/skills/import", json={"text": "没有 frontmatter"},
                          headers=admin_headers)
    assert r.status_code == 422


async def test_delete_builtin_conflict(client, admin_headers):
    assert (await client.delete("/api/v1/skills/data-analysis",
                                headers=admin_headers)).status_code == 409
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

```python
"""技能 API：全局技能目录的增删改查 + SKILL.md 导入导出。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.api.deps import get_current_user, require_admin
from app.services.skill_service import parse_skill_md, render_skill_md

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])


class SkillBody(BaseModel):
    """技能字段（content 为 SKILL.md 正文，不含 frontmatter）。"""

    name: str
    description: str
    content: str
    version: str = "1.0"
    author: str = ""
    tags: list[str] = []
    allowed_tools: list[str] = []


class SkillImportBody(BaseModel):
    """导入请求体。"""

    text: str


@router.get("")
async def list_skills(request: Request, user=Depends(get_current_user)):
    """列出全部技能。"""
    return request.app.state.skill_service.list_skills()


@router.get("/{name}/export", response_class=PlainTextResponse)
async def export_skill(request: Request, name: str, user=Depends(get_current_user)):
    """导出 SKILL.md 原文。"""
    for skill in request.app.state.skill_service.list_skills():
        if skill["name"] == name:
            return PlainTextResponse(render_skill_md(skill))
    raise HTTPException(status_code=404, detail="技能不存在")


@router.post("", status_code=201)
async def create_skill(request: Request, body: SkillBody, user=Depends(require_admin)):
    """新建技能（写 {data_root}/skills/<name>/SKILL.md）。"""
    try:
        return request.app.state.skill_service.write_skill(
            name=body.name, description=body.description, content=body.content,
            version=body.version, author=body.author,
            tags=body.tags, allowed_tools=body.allowed_tools)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/{name}")
async def update_skill(request: Request, name: str, body: SkillBody, user=Depends(require_admin)):
    """覆盖写技能（技能名不可改，改名前请另建再删）。"""
    try:
        return request.app.state.skill_service.write_skill(
            name=name, description=body.description, content=body.content,
            version=body.version, author=body.author,
            tags=body.tags, allowed_tools=body.allowed_tools)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{name}")
async def delete_skill(request: Request, name: str, user=Depends(require_admin)):
    """删除技能目录。"""
    try:
        ok = request.app.state.skill_service.delete_skill(name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="技能不存在")
    return {"ok": True}


@router.post("/import", status_code=201)
async def import_skill(request: Request, body: SkillImportBody, user=Depends(require_admin)):
    """从 SKILL.md 文本导入。"""
    try:
        parsed = parse_skill_md(body.text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return request.app.state.skill_service.write_skill(
        name=parsed["name"], description=parsed["description"], content=parsed["content"],
        version=parsed["version"], author=parsed["author"],
        tags=parsed["tags"], allowed_tools=parsed["allowed_tools"])
```

`main.py` 里：

```python
app.state.skill_service = SkillService(settings.data_root)
app.state.skill_service.seed_builtins()      # 幂等播种内置技能
app.include_router(skills_router)
```

> **路由顺序坑**：`/import` 必须注册在 `/{name}/export` 之前吗？——不必，两者路径段数不同（1 vs 2），FastAPI 不会混淆。但若将来加 `GET /{name}`，需把 `/import` 放前面。

- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: Commit** — `feat(web): 技能 API（磁盘 SKILL.md 读写 + 导入导出）`

---

## Task B5: agent_service 装配（提示词 + 技能注入）

**Files:** Modify `app/services/agent_service.py`；Test `tests/test_chat_api.py`（追加）

- [ ] **Step 1: 写失败测试**（用 httpx 抓 system prompt —— 现有测试已有 mock backend，断言其收到的首条 system 消息）

```python
async def test_system_prompt_contains_platform_sections_and_persona(client, auth_headers, mock_backend):
    await client.post("/api/v1/sessions", json={"assistant_id": "asst-research"}, headers=auth_headers)
    # 触发一轮对话后，mock backend 捕获到的 system 消息
    captured = mock_backend.last_system_prompt
    assert "核心原则" in captured            # 平台默认段
    assert "科研助手" in captured            # 专家 persona 追加
    assert str(tmp_workspace) in captured     # 工作区段


async def test_skill_index_injected_when_skills_present(client, auth_headers, mock_backend):
    ...
    assert "`data-analysis`" in mock_backend.last_system_prompt
    assert "data-analysis" in mock_backend.last_tools_names      # skill.read 已在工具表
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

`agent_service.chat(...)` 装配处改为：

```python
# 全局技能目录（磁盘扫描）+ 本会话选中的技能名（由 A5 随请求透传；空 = 全部可用）
all_skills = self._skill_service.list_skills()
if requested_skills:                      # None/[] 都表示"用全部"
    active = [s for s in all_skills if s["name"] in set(requested_skills)]
else:
    active = all_skills
index = [(s["name"], s["description"]) for s in active]
bodies = {s["name"]: self._skill_service.read_body(s["name"]) or "" for s in active}

system_prompt = build_system_prompt(
    persona=assistant["system_prompt"],
    workspace=workspace_root,
    skills=index,
)
config = AgentConfig(
    system_prompt=system_prompt,
    tool_names=[*assistant.get("tool_whitelist") or [], "skill.list", "skill.read"],
    max_steps=assistant.get("max_steps", 25),
)
...
context_extra={
    "http_allowed_hosts": self._settings.allowed_hosts,
    "skills": bodies,                      # skill.read 按名取正文
    "skill_meta": {s["name"]: s["description"] for s in active},
}
```

`agent_service` 的构造函数补 `skill_service: SkillService` 依赖（`main.py` 里与 `project_service` 一起注入）。

- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: Commit** — `feat(web): 装配平台提示词与技能渐进披露`

---

## Task B6: 技能管理页 + 助手表单补工具项

**Files:** Create `components/admin/SkillsAdmin.tsx`；Modify `components/admin/AssistantsAdmin.tsx`（`TOOL_LABELS` 加 `skill.list` / `skill.read`）、`App.tsx`（`#/admin/skills` 路由）、`AdminLayout.tsx`（页签）

- [ ] **Step 1: SkillsAdmin**：列表（名称/描述/标签/builtin 徽标/操作）+ 表单弹窗（name/display_name/description/version/author/tags/allowed_tools/content 多行）+「导入 SKILL.md」按钮（textarea 粘贴 → `POST /skills/import`）+ 导出。
- [ ] **Step 2: AssistantsAdmin** 的 `TOOL_NAMES` 补上新工具，否则白名单校验 422。
- [ ] **Step 3: 验证**：playwright 有头模式进 `#/admin/skills`，建一条技能 → 工作台选中 → 发消息 → 后端日志中 system prompt 含技能索引。
- [ ] **Step 4: Commit** — `feat(frontend): 技能管理页`

---

# Phase A：输入框改造（可见收口）

## Task A1: 模型选择器移到发送键左侧

**Files:** Modify `components/chat/Composer.tsx`、`components/chat/ModelPicker.tsx`

- [ ] **Step 1:** `Composer.tsx` 底部行改成 `justify-between`：左 = `+` 按钮 + 专家 chip + 技能 chips；右 = `<ModelPicker compact />` + 发送/停止按钮（形成 `chat-input-actions`）。
- [ ] **Step 2:** `ModelPicker` 加 `compact` 变体（收窄内边距、`text-[12.5px]`），保持现有下拉行为。
- [ ] **Step 3: 验证**：`npm run build`；playwright 截图对比 jiuwen（`InputArea.tsx:3568` 的层级）。
- [ ] **Step 4: Commit** — `style(frontend): 模型选择器移至发送键左侧`

---

## Task A2: `+` 菜单骨架（portal + 定位 + 外点关闭）

**Files:** Create `components/chat/AttachMenu.tsx`；Modify `components/chat/Composer.tsx`

- [ ] **Step 1: 实现**（照 `InputArea.tsx:2940-3000`）

```tsx
/** 输入框 + 弹层：portal + fixed 定位（空间不足时向上弹）。 */
export default function AttachMenu({ items }: { items: AttachMenuItem[] }) {
  const anchorRef = useRef<HTMLButtonElement>(null)
  const portalRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [rect, setRect] = useState<DOMRect | null>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => {
      if (anchorRef.current?.contains(e.target as Node)) return
      if (portalRef.current?.contains(e.target as Node)) return
      setOpen(false)
    }
    document.addEventListener('pointerdown', onDown)
    return () => document.removeEventListener('pointerdown', onDown)
  }, [open])
  ...
}
```

菜单容器 class 与样式照 `ChatPanel.css:1598` 的 `.chat-mode-select__menu` 翻译成 Tailwind（`min-w-[180px] p-2 rounded-[10px] shadow-lg border`）；上级菜单项用 `ChatPanel.css:1811` 的 `left: calc(100% + 11px)` 二级定位。

- [ ] **Step 2: 菜单项**：上传文件 / 专家 › / 技能 ›（`role="menu"`，二级项带 `ChevronRight`，分隔线 `role="separator"`）。
- [ ] **Step 3: 验证**：playwright 点 `+` 出菜单、点外部关闭、视口底部时向上弹。
- [ ] **Step 4: Commit** — `feat(frontend): 输入框 + 弹层（portal + 定位 + 外点关闭）`

---

## Task A3: 专家面板 + 会话级切换

**Files:** Create `components/chat/ExpertPicker.tsx`；Modify `stores/assistants.ts`、`stores/sessions.ts`、`components/chat/Composer.tsx`

- [ ] **Step 1:** 新增 `PATCH /api/v1/sessions/{sid}` 已支持改字段——**先补一个 `assistant_id` 可改**（后端 `SessionUpdateBody` 增字段 + 校验助手存在）。
- [ ] **Step 2:** `ExpertPicker`：搜索框 + 助手列表（头像/名/描述），选中打勾 → 调 `sessions.setAssistant(sid, id)` → 刷新会话标题栏助手名。空会话选中即作为该会话专家；有消息的会话切换后**下一轮**生效（提示词在下一 turn 才重算）。
- [ ] **Step 3:** `Sidebar.tsx` 移除助手卡与 `selectedId` 相关逻辑；`assistants.ts` 删掉 `selectedId/select/pickSelectedAssistant`。
- [ ] **Step 4: 验证**：无会话时点专家 → 先建会话再绑定；有会话时切换 → 顶栏助手名变化。
- [ ] **Step 5: Commit** — `feat(frontend): 专家选择迁入 + 菜单，移除侧栏助手卡`

---

## Task A4: 上传文件接进 `+` 菜单

**Files:** Modify `components/chat/AttachMenu.tsx`、`Composer.tsx`

- [ ] **Step 1:** 菜单「上传文件」触发隐藏 `<input type="file" multiple>`；上传目标 = 当前项目的 `files/`（`POST /api/v1/projects/{pid}/files`）。
- [ ] **Step 2:** 上传成功后 toast + 刷新右栏目录树；失败按 `results[].code` 给出 422/413 文案（复用 `Composer` 已有的错误条）。
- [ ] **Step 3: 验证**：playwright 上传 txt 出现在树里；上传 `.exe` 被拒。
- [ ] **Step 4: Commit** — `feat(frontend): 输入框 + 菜单上传文件`

---

## Task A5: 技能面板接上真实数据

**Files:** Create `components/chat/SkillPicker.tsx`；Modify `Composer.tsx`、`stores/chat.ts`

- [ ] **Step 1:** `SkillPicker` 照 `SkillPickerPanel.tsx`：搜索 + 列表 + 选中打勾，数据来自 `stores/skills.ts`（`GET /api/v1/skills`）。
- [ ] **Step 2:** 已选技能在输入框上方渲染为可删除 chip；随发送请求带上（`POST /sessions/{sid}/messages` body 增 `skills: string[]`），后端按名过滤注入 `context_extra["skills"]` 与技能索引；**发送后清空**（jiuwen 的一次性语义）。
- [ ] **Step 3: 验证**：选 `data-analysis` → 发送 → 后端日志显示该技能正文可被 `skill.read` 读到、且索引进了 system prompt。
- [ ] **Step 4: Commit** — `feat(frontend): 技能选择器与会话技能透传`

---

# 验收（三阶段全绿后）

- [ ] `cd packages/synlys-harness && conda run -n synlysagent --no-capture-output python -m pytest -v`
- [ ] `cd apps/web/backend && conda run -n synlysagent --no-capture-output python -m pytest -v`
- [ ] `cd apps/web/frontend && npm run build && npm run lint`
- [ ] playwright **有头模式**逐场景截图自查：
  1. 空会话：输入框居中，左下有 `+`，右下模型选择器紧贴发送键
  2. `+` → 专家：切换后顶栏助手名变化
  3. `+` → 上传文件：文件出现在右栏项目树的 `files/` 下
  4. `+` → 技能：选中后 chip 出现，发送后后端把技能索引拼进 system prompt
  5. 右栏：新建项目 → 目录树展示 `files/output/tmp` → 展开子目录懒加载
  6. 旧数据：原 `workspaces/dev/files` 已迁到 `workspaces/dev/default/files`
- [ ] 版本号按语义化规则升次版本（新功能向下兼容）：`0.1.0 → 0.2.0`（`package.json` / `pyproject.toml` 同步）

---

## 自检

**需求覆盖**

| 用户要求 | 覆盖任务 |
|---|---|
| 专家选择挪进聊天窗 `+` 菜单 | A2 / A3 |
| 上传文件加进 `+` 菜单 | A4 |
| 技能占位→完整实现（参考 jiuwen 框架） | B1–B6、A5 |
| 后台可配置（专家、技能） | B4/B6（技能页）；专家沿用既有 `AssistantsAdmin` |
| 一套默认系统提示词，选专家后可配置 | B1（平台段）+ B5（persona 追加） |
| 模型选择按钮放到发送键左侧 | A1 |
| 右侧文件改工作区模式、可新建项目 | C2/C3/C4/C7 |
| 目录结构 `用户名/项目名/子目录` | C1 |

**未覆盖 / 有意省略（需你确认是否接受）**
- 「扩展」菜单项（jiuwen 有）本轮不做。
- 「计划模式 / 追求目标」开关（jiuwen 有）本轮不做。
- jiuwen 的 `skill_index` 结构化检索、marketplace、技能版本管理本轮不做，只做 `skill.list` + `skill.read`。
- DSH 的 `dsh-skill` 多来源 provider 注册表 + 技能目录**文件监听（watcher）**本轮不做——技能改动后需刷新页面/重进会话才生效（jiuwen 同样需要重扫）。
- 项目改名本轮只做 API（`PATCH /projects/{id}`），不做前端入口。
- 技能目录里的 `scripts/ references/ assets/` 不做编辑 UI（可手动放文件；`skill.read` 只读 `SKILL.md` 正文）。

**类型一致性**：`TreeEntry`（C7）字段与 `list_dir` 返回（C3）一致；`Project` 字段与 `ProjectRepo.create`（C2）一致；`build_system_prompt` 签名在 B1 定义、B5 调用一致。
