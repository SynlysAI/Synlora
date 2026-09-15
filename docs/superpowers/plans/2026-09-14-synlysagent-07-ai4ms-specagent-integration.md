# AI⁴MS SpecAgent 接入（插件化版 v2）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以「一切皆插件」的方式接入 Spec_Agent 核磁预测三件套：宿主提供一次性通用插件框架（扫描 + 配置 + 安装 API + 管理页），插件包自包含（manifest + 工具 + 技能 + 专家模板）；此后新增任何子平台 = 只加一个插件目录，主框架与 harness 零改动。

**Architecture:** 插件包放 `apps/web/backend/plugins/<id>/`（自带 `plugin.json` 声明配置 schema、工具模块、技能、专家模板）；宿主通用框架 `app/plugins/`（loader 扫描与加载、config_store 配置落库且敏感字段 Fernet 加密、PluginService 编排安装/注入、api 暴露管理端点）；配置**不进** `settings.py`/`.env`，由管理员在插件管理页按 schema 填写、落库加密；运行期把已安装插件的解密配置按命名空间注入 `ctx.extra["plugins"]`；技能由插件目录提供（SkillService 支持额外技能根，不硬编码技能名）。

**Tech Stack:** Python 3.12 / FastAPI / pydantic（配置校验）/ cryptography Fernet（复用 `app/core/crypto.py`）/ importlib 动态加载插件模块 / pytest；前端 React 19 + TS + Tailwind 4 + Zustand（手写路由）

**前置修正（v1 的错误，已被用户否决）：** v1 把 `ai4ms_providers`/`spec_agent_base_url`/`spec_agent_token` 写进 `app/core/settings.py`，并把技能名硬编码进 `skill_service.BUILTIN_SKILL_NAMES` —— 两处都侵入主框架，Task 1 回退。

---

## 一、参考结论（设计依据，勿自行发挥）

| 维度 | jiuwen 做法（本计划照抄对象） | DSH(Cordis) 做法 |
|---|---|---|
| 插件形态 | 目录 + `manifest.json`，内含 `persona/` `skills/` `tools/` | npm 包导出 `apply(ctx)` + `static Config`(schema) |
| 宿主加载 | **通用目录扫描 + 读 manifest**，不为单个插件写分支代码 | 配置列表 + 运行期 import |
| 配置存放 | 每插件独立加密凭证文件（AES-256-GCM，按 name 隔离） | 用户配置按 namespace 分节，**schema 由插件自带** |
| 敏感项 | 独立 CredentialStore；专家包禁止携带凭证 | 独立 credentials seam，插件只持**引用**，不物化进 env |
| 前端表单 | **后端返回 fields schema → 前端渲染**（`ConnectTokenModal.tsx`） | `schema.toJSON()` 给前端渲染 |
| 启用控制 | 只认 `installed`，无全局 env 开关 | 组合配置行 `disabled` 字段 |

## 二、关键事实（本项目现状，写代码前必须知道）

- `app/core/crypto.py`：`encrypt_key(plain, fernet_key) -> (stored, encrypted_bool)`、`decrypt_key(stored, fernet_key, encrypted) -> plain`；Fernet key 来自 `settings.fernet_key`（空 = 明文，开发模式）。
- `app/db/store.py` DocumentStore 通用接口：`insert(collection, doc)` / `get(collection, id)` / `list(collection, filters=, sort=, limit=)` / `update(collection, id, fields)` / `delete(collection, id)`。**新集合必须在 `COLLECTION_INDEXES` 注册**（sqlite 后端只为清单内集合建表；Mongo 无需迁移）。Task 3 顺带修了该文件一个既有 bug：索引列为空时建表 SQL 生成 `... , )` 语法错误（既有集合都有索引故从未触发）。
- `app/api/deps.py`：`get_current_user` / `require_admin`；`Repos` 是 frozen dataclass（**本次不加字段**，插件端点自带依赖函数从 `app.state` 取）。
- 共享工具注册表：`app/services/tool_registry.py` 的 `REGISTRY`（单实例，运行装配与助手白名单校验共用）；`ToolPipeline(registry=REGISTRY)` 每次调用按名解析，支持运行期注册。
- `assistants_api._validate_tool_whitelist` 用 `REGISTRY.names` 校验，助手 `create` 传 `_id` 可指定 id（`BaseRepo.create` 尊重传入 `_id`）。
- 前端：手写路由（`src/routing/route.ts` 的 `AdminTab` 联合 + `parseAppRoute` 正则）；管理页三件套 `ModelsAdmin/AssistantsAdmin/SkillsAdmin`；**模型服务页的 api_key 就是 secret 字段的照抄对象**（`ModelsAdmin.tsx:141-152` 密码输入框、编辑时留空不覆盖、`KeyBadge` 已配置徽标）；基础组件在 `src/components/admin/shared.tsx`（`Modal`/`GrayBadge`/`FormError`/`Spinner`），样式常量在 `src/components/admin/form.ts`（`inputClass`/`labelClass`/`primaryButtonClass`/`errorText`）；API 封装 `src/api/client.ts` 的 `api<T>(path, {method, body})`；类型放 `src/types.ts`。

## 三、File Structure

| 动作 | 文件 | 职责 |
|---|---|---|
| 回退 | `app/core/settings.py` | 删掉 v1 加入的 3 个 AI⁴MS 配置项 |
| 删除 | `app/integrations/`（整个目录）、`tests/test_tool_registry.py` | v1 骨架作废（v2 由 Task 3 重建测试） |
| 创建 | `app/plugins/__init__.py` | 插件框架包入口（导出 loader/service 类型） |
| 创建 | `app/plugins/loader.py` | 扫描插件包、解析 manifest、加载插件工具 |
| 创建 | `app/plugins/config_store.py` | 插件配置落库（敏感字段 Fernet 加密、留空保持原值） |
| 创建 | `app/plugins/service.py` | `PluginService`：安装/配置/上下文注入/专家播种 |
| 创建 | `app/plugins/api.py` | `GET /api/v1/plugins`、`POST /{id}/install`、`PUT /{id}/config` |
| 创建 | `plugins/spec_agent/plugin.json` | Spec_Agent 插件 manifest（配置 schema + 专家模板） |
| 创建 | `plugins/spec_agent/tools.py` | 核磁三件套工具（harness `@tool`） |
| 创建 | `plugins/spec_agent/skills/spec-nmr/SKILL.md` | 插件自带技能 |
| 创建 | `tests/test_plugin_loader.py`、`tests/test_plugin_config.py`、`tests/test_plugin_service.py`、`tests/test_plugins_api.py` | 后端测试 |
| 创建 | `src/components/admin/PluginsAdmin.tsx` | 插件管理页（schema 驱动表单） |
| 修改 | `app/services/tool_registry.py` | 保留单实例收口（v1 Task 2 的成果，v2 继续） |
| 修改 | `app/services/agent_service.py` | 用共享注册表；注入 `ctx.extra["plugins"]` |
| 修改 | `app/api/assistants_api.py` | 用共享注册表 |
| 修改 | `app/services/skill_service.py` | 支持额外技能根（插件技能不落用户技能目录） |
| 修改 | `app/main.py` | lifespan 装配插件框架 + include 路由 |
| 修改 | `src/routing/route.ts`、`src/App.tsx`、`src/components/admin/AdminLayout.tsx`、`src/components/admin/index.ts`、`src/stores/admin.ts`、`src/types.ts` | 前端接入插件页 |
| 修改 | `README.md`、`apps/web/backend/README.md`、`CLAUDE.md`、backlog、`app/version.py`、`apps/web/frontend/package.json` | 文档与版本 |

命令工作目录：后端 `E:\agent_projects\Synlora\apps\web\backend`，前端 `E:\agent_projects\Synlora\apps\web\frontend`。所有文件操作使用**完整绝对 Windows 路径**。

---

## Task 1: 回退 v1 侵入改动 + 插件包扫描与加载

**Files:**
- Modify: `apps/web/backend/app/core/settings.py`（删 3 字段 + 3 行 docstring）
- Delete: `apps/web/backend/app/integrations/`（整个目录）
- Modify: `apps/web/backend/app/services/tool_registry.py`、`apps/web/backend/tests/test_tool_registry.py`（**工作区已存在 v1 版的注册表收口改动，本任务就地适配为 v2 形态，不做丢弃重做**；等价于原 Task 2 的内容提前到本任务，故 Task 2 已无独立工作）
- Create: `apps/web/backend/app/plugins/__init__.py`、`apps/web/backend/app/plugins/loader.py`
- Test: `apps/web/backend/tests/test_plugin_loader.py`

> 背景：工作区存在未提交的 v1 Task 2 成果（`app/services/tool_registry.py` + `assistants_api.py`/`agent_service.py`/`test_chat_api.py`/`test_tool_registry.py` 的改动），其中 `tool_registry.py` 依赖将被删除的 `app.integrations`。处理原则：**保留并升级**——`assistants_api.py`/`agent_service.py`/`test_chat_api.py` 的改动与 v2 一致，原样保留；`tool_registry.py` 去掉 settings/integrations 依赖；`test_tool_registry.py` 删掉依赖 integrations 的用例、保留 `test_shared_registry_singleton`、补 v2 的两条新用例。

- [ ] **Step 1: 回退 settings 与 v1 骨架**

从 `app/core/settings.py` 删除这三行字段：

```python
    ai4ms_providers: str = ""
    spec_agent_base_url: str = ""
    spec_agent_token: str = ""
```

并删除类 docstring 里对应的三行 Attributes 说明（`ai4ms_providers:` / `spec_agent_base_url:` / `spec_agent_token:` 开头的那几行）。

然后删除 v1 的插件骨架与测试（v2 不再叫 integrations）：

```bash
rm -rf "E:/agent_projects/Synlora/apps/web/backend/app/integrations"
rm -f "E:/agent_projects/Synlora/apps/web/backend/tests/test_tool_registry.py"
```

（用 `git rm -r` 亦可；注意 `git status` 应显示这两个路径被删除。）

- [ ] **Step 2: 写失败测试**

创建 `apps/web/backend/tests/test_plugin_loader.py`：

```python
"""插件包扫描与工具加载测试。"""
from __future__ import annotations

import json

from app.core.settings import Settings
from app.plugins.loader import (
    PluginPackage,
    load_plugin_tools,
    plugin_roots,
    scan_plugins,
)

TOOLS_SOURCE = '''
"""测试插件工具模块。"""
from synlys_harness import ToolContext, ToolResult, tool


@tool(name="demo.hello", description="示例工具",
      parameters={"type": "object", "properties": {}})
async def demo_hello(ctx: ToolContext, args: dict) -> ToolResult:
    """示例工具：回 hi。"""
    return ToolResult(ok=True, content="hi")
'''

MANIFEST = {
    "id": "demo",
    "name": "示例插件",
    "version": "1.0.0",
    "description": "用于测试的插件包",
    "tools_module": "tools.py",
    "config_schema": [
        {"key": "base_url", "label": "服务地址", "type": "text", "required": True},
        {"key": "token", "label": "凭证", "type": "password", "secret": True},
    ],
    "skills": ["demo-skill"],
    "expert": {"name": "示例专家", "system_prompt": "你是示例专家。",
               "tool_whitelist": ["demo.hello"]},
}


def _make_package(root, plugin_id: str = "demo", manifest: dict | None = None):
    """在 root 下造一个插件包目录。

    Args:
        root: 插件根目录。
        plugin_id: 插件目录名与 manifest id。
        manifest: manifest 内容（None 用 MANIFEST）。

    Returns:
        插件目录 Path。
    """
    d = root / plugin_id
    d.mkdir(parents=True)
    (d / "plugin.json").write_text(
        json.dumps(manifest or MANIFEST, ensure_ascii=False), encoding="utf-8")
    (d / "tools.py").write_text(TOOLS_SOURCE, encoding="utf-8")
    return d


def test_plugin_roots_include_repo_and_data_dir(tmp_path):
    """插件根 = 随仓库的 plugins/ + 数据目录下的 plugins/。"""
    settings = Settings(data_dir=str(tmp_path))
    roots = plugin_roots(settings)
    assert roots[0].name == "plugins" and roots[0].parent.name == "backend"
    assert roots[1] == tmp_path / "plugins"


def test_scan_plugins_parses_manifest(tmp_path):
    """扫描解析出完整 PluginPackage（含 schema/技能/专家模板）。"""
    _make_package(tmp_path)
    packages = scan_plugins([tmp_path])
    pkg = packages["demo"]
    assert isinstance(pkg, PluginPackage)
    assert pkg.name == "示例插件" and pkg.tools_module == "tools.py"
    assert [f["key"] for f in pkg.config_schema] == ["base_url", "token"]
    assert pkg.skills == ["demo-skill"]
    assert pkg.expert["name"] == "示例专家"
    assert pkg.skills_root == tmp_path / "demo" / "skills"  # 无 skills/ 时不报错，仅目录不存在


def test_scan_plugins_skips_malformed(tmp_path):
    """manifest 非法（JSON 坏 / 缺必填字段）只跳过，不抛异常。"""
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "plugin.json").write_text("{ not json", encoding="utf-8")
    missing = tmp_path / "missing"
    missing.mkdir()
    (missing / "plugin.json").write_text(json.dumps({"id": "x"}), encoding="utf-8")
    (tmp_path / "not-a-plugin").mkdir()  # 无 manifest 的目录

    assert scan_plugins([tmp_path]) == {}


def test_scan_plugins_missing_root_is_fine(tmp_path):
    """根目录不存在时返回空表（首次部署无 plugins/ 目录）。"""
    assert scan_plugins([tmp_path / "nope"]) == {}


def test_load_plugin_tools_collects_decorated_functions(tmp_path):
    """加载工具模块，收集带 __tool_definition__ 的函数。"""
    _make_package(tmp_path)
    pkg = scan_plugins([tmp_path])["demo"]
    tools = load_plugin_tools(pkg)
    assert [t.__tool_definition__.name for t in tools] == ["demo.hello"]


def test_load_plugin_tools_missing_module_returns_empty(tmp_path):
    """工具模块缺失时返回空列表（不抛异常）。"""
    d = _make_package(tmp_path)
    (d / "tools.py").unlink()
    pkg = scan_plugins([tmp_path])["demo"]
    assert load_plugin_tools(pkg) == []
```

`PluginPackage.skills_root` 的语义（两条测试都覆盖）：目录不存在时 `skills_root is None`；存在时返回 Path。

- [ ] **Step 3: 运行测试确认失败**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_plugin_loader.py -v
```

预期：`ModuleNotFoundError: No module named 'app.plugins'`。

- [ ] **Step 4: 实现 loader**

创建 `apps/web/backend/app/plugins/__init__.py`：

```python
"""插件框架：通用插件包扫描、配置存储与安装编排。

宿主只认「插件包目录 + plugin.json」这一通用约定，不为任何单个插件写分支
代码；新增子平台 = 新增一个插件目录，本包与 harness 均不改（参考 jiuwen
的 manifest 目录扫描与 DSH 的插件即插即用）。
"""
```

创建 `apps/web/backend/app/plugins/loader.py`：

```python
"""插件包扫描与工具加载。

插件包 = 一个目录 + `plugin.json`（manifest）。宿主按通用约定扫描根目录、
解析 manifest、按 `tools_module` 动态导入工具函数（harness `@tool` 装饰过）。
manifest 非法只跳过并告警，不阻断服务启动。
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.settings import Settings

logger = logging.getLogger(__name__)

MANIFEST_NAME = "plugin.json"
REQUIRED_FIELDS = ("id", "name", "version", "tools_module")


@dataclass(frozen=True)
class PluginPackage:
    """一个插件包（插件目录 + 解析后的 manifest）。

    Attributes:
        id: 插件 id（= 目录名，工具配置命名空间）。
        name: 显示名。
        version: 版本号。
        description: 描述。
        directory: 插件目录绝对路径。
        tools_module: 工具模块文件名（相对插件目录）。
        config_schema: 配置字段 schema（驱动前端表单与校验）。
        skills: 插件自带技能名列表。
        expert: 专家模板（None = 本插件不贡献专家）。
    """

    id: str
    name: str
    version: str
    description: str
    directory: Path
    tools_module: str
    config_schema: list[dict] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    expert: dict | None = None

    @property
    def skills_root(self) -> Path | None:
        """插件自带技能目录（不存在时 None）。"""
        d = self.directory / "skills"
        return d if d.is_dir() else None


def plugin_roots(settings: "Settings") -> list[Path]:
    """插件包搜索根。

    Args:
        settings: 应用配置（取数据目录）。

    Returns:
        根目录列表：随仓库的 `apps/web/backend/plugins/` + 数据目录下
        `{data_dir}/plugins/`（后者为运行期安装预留）。
    """
    return [
        Path(__file__).resolve().parents[2] / "plugins",
        settings.data_root / "plugins",
    ]


def scan_plugins(roots: list[Path]) -> dict[str, PluginPackage]:
    """扫描插件根目录，解析全部合法插件包。

    Args:
        roots: 插件根目录列表（不存在的根直接跳过）。

    Returns:
        {插件 id: PluginPackage}；非法 manifest 只告警跳过。
    """
    packages: dict[str, PluginPackage] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            manifest_path = entry / MANIFEST_NAME
            if not manifest_path.is_file():
                continue
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("插件 manifest 解析失败，已跳过 %s: %s", entry.name, exc)
                continue
            missing = [k for k in REQUIRED_FIELDS if not data.get(k)]
            if missing:
                logger.warning("插件 manifest 缺字段 %s，已跳过 %s", missing, entry.name)
                continue
            packages[data["id"]] = PluginPackage(
                id=str(data["id"]),
                name=str(data["name"]),
                version=str(data["version"]),
                description=str(data.get("description") or ""),
                directory=entry,
                tools_module=str(data["tools_module"]),
                config_schema=list(data.get("config_schema") or []),
                skills=[str(s) for s in (data.get("skills") or [])],
                expert=data.get("expert") or None,
            )
    return packages


def load_plugin_tools(package: PluginPackage) -> list[Any]:
    """动态导入插件工具模块并收集 `@tool` 装饰过的函数。

    Args:
        package: 插件包。

    Returns:
        工具函数列表（模块缺失或导入失败时返回空列表并告警）。
    """
    module_path = package.directory / package.tools_module
    if not module_path.is_file():
        logger.warning("插件 %s 的工具模块不存在: %s", package.id, module_path)
        return []
    module_name = f"synlora_plugin_{package.id}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            logger.warning("插件 %s 工具模块无法加载: %s", package.id, module_path)
            return []
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception as exc:
        logger.warning("插件 %s 工具模块导入失败: %s", package.id, exc)
        return []
    return [obj for obj in vars(module).values()
            if hasattr(obj, "__tool_definition__")]
```

修正 `test_scan_plugins_parses_manifest` 里对 `skills_root` 的断言：无 `skills/` 目录时为 `None`，请把断言写成：

```python
    assert pkg.skills_root is None  # 包内无 skills/ 目录
```

并在该测试里补一个建有 `skills/demo-skill/SKILL.md` 的用例（或直接断言另一个包）验证目录存在时返回 Path：

```python
def test_skills_root_present_when_dir_exists(tmp_path):
    """包内有 skills/ 目录时 skills_root 指向它。"""
    d = _make_package(tmp_path)
    (d / "skills" / "demo-skill").mkdir(parents=True)
    (d / "skills" / "demo-skill" / "SKILL.md").write_text("---\nname: demo-skill\ndescription: 示例\n---\n正文\n", encoding="utf-8")
    pkg = scan_plugins([tmp_path])["demo"]
    assert pkg.skills_root == d / "skills"
```

- [ ] **Step 5: 运行测试确认通过**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_plugin_loader.py -v
```

预期：8 passed。

- [ ] **Step 6: 提交**

```bash
cd "E:\agent_projects\Synlora" && git add -A apps/web/backend/app apps/web/backend/tests && git commit -m "refactor: 回退 settings 中的插件配置，改为通用插件包扫描与加载"
```

---

## Task 2: 共享工具注册表收口（已在 Task 1 一并完成）

> 本任务内容已合并进 Task 1（工作区既有 v1 收口成果就地适配为 v2 形态）。执行时跳过本任务，直接进入 Task 3。

**Files:**
- Create: `apps/web/backend/app/services/tool_registry.py`
- Modify: `apps/web/backend/app/services/agent_service.py:30-63`
- Modify: `apps/web/backend/app/api/assistants_api.py:6-13`
- Test: `apps/web/backend/tests/test_tool_registry.py`

- [ ] **Step 1: 写失败测试**

创建 `apps/web/backend/tests/test_tool_registry.py`：

```python
"""共享工具注册表测试（运行装配与白名单校验必须同一实例）。"""
from __future__ import annotations


def test_shared_registry_singleton():
    """两侧取到同一实例，且含内置工具。"""
    from app.api import assistants_api
    from app.services import agent_service
    from app.services.tool_registry import PIPELINE, REGISTRY

    assert assistants_api._REGISTRY is REGISTRY
    assert agent_service._REGISTRY is REGISTRY
    assert agent_service._PIPELINE is PIPELINE
    assert PIPELINE._registry is REGISTRY
    assert "python.run" in REGISTRY.names
    assert "file.read" in REGISTRY.names


def test_registry_is_empty_of_plugin_tools_by_default():
    """未安装任何插件时，注册表里没有插件工具（宿主启动时才注册）。"""
    from app.services.tool_registry import REGISTRY

    assert [n for n in REGISTRY.names if n.startswith("spec.")] == []


def test_registry_supports_runtime_registration():
    """注册表支持运行期注册/注销（插件安装/卸载依赖这一能力）。"""
    from synlys_harness import ToolContext, ToolResult, tool
    from app.services.tool_registry import REGISTRY

    @tool(name="tmp.demo", description="临时工具",
          parameters={"type": "object", "properties": {}})
    async def tmp_demo(ctx: ToolContext, args: dict) -> ToolResult:
        """临时工具。"""
        return ToolResult(ok=True, content="ok")

    REGISTRY.register(tmp_demo)
    try:
        assert "tmp.demo" in REGISTRY.names
        assert REGISTRY.find("tmp.demo") is not None
    finally:
        REGISTRY.unregister("tmp.demo")
    assert "tmp.demo" not in REGISTRY.names
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_tool_registry.py -v
```

预期：`ModuleNotFoundError: No module named 'app.services.tool_registry'`。

- [ ] **Step 3: 创建收口模块**

创建 `apps/web/backend/app/services/tool_registry.py`：

```python
"""工具注册表收口（单实例，运行装配与助手白名单校验共用）。

agent_service（装配 RunSession 用）与 assistants_api（校验助手工具白名单用）
必须看到同一份工具集：插件注册的工具名若只在一边可见，白名单校验会把它们
判为"未注册"而 422。

内置工具在模块导入期注册；插件工具在 lifespan 装配时（按已安装插件）或
安装 API 调用时注册——注册表支持运行期增删（见 ToolRegistry.register）。
"""
from __future__ import annotations

from synlys_harness import ToolPipeline, ToolRegistry, register_builtin_tools


def build_registry() -> ToolRegistry:
    """构建含全部内置工具的注册表（插件工具由装配阶段另行注册）。

    Returns:
        新的 ToolRegistry。
    """
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return registry


REGISTRY = build_registry()
PIPELINE = ToolPipeline(registry=REGISTRY)
```

- [ ] **Step 4: 改造两个消费方**

`app/services/agent_service.py`：

1. 从 harness 导入列表里**删除** `ToolPipeline`、`ToolRegistry`、`register_builtin_tools` 三个名字（其余保留）。
2. 在 `from app.services.skill_service import SkillService` 之后追加：

```python
from app.services.tool_registry import PIPELINE as _PIPELINE, REGISTRY as _REGISTRY
```

3. 删除模块级的三行构建：

```python
_REGISTRY = ToolRegistry()
register_builtin_tools(_REGISTRY)
_PIPELINE = ToolPipeline(registry=_REGISTRY)
```

替换为一行注释：

```python
# 工具注册表收口在 app.services.tool_registry（与 assistants_api 共用同一实例）
```

`app/api/assistants_api.py`：

1. 删除 `from synlys_harness import ToolRegistry, register_builtin_tools` 整行。
2. 把

```python
_REGISTRY = ToolRegistry()
register_builtin_tools(_REGISTRY)
```

替换为（放在 `from app.api.deps import ...` 之后）：

```python
from app.services.tool_registry import REGISTRY as _REGISTRY
```

- [ ] **Step 5: 运行全量后端测试确认无回归**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest -q
```

预期：全部通过（既有 207+ 项不受影响；`test_invalid_tool_whitelist_422` 仍 422）。

- [ ] **Step 6: 提交**

```bash
cd "E:\agent_projects\Synlora" && git add apps/web/backend/app/services/tool_registry.py apps/web/backend/app/services/agent_service.py apps/web/backend/app/api/assistants_api.py apps/web/backend/tests/test_tool_registry.py && git commit -m "refactor: 工具注册表收口为单一共享实例（运行与白名单校验一致）"
```

---

## Task 3: 插件配置存储（加密 + 留空保持原值）

**Files:**
- Create: `apps/web/backend/app/plugins/config_store.py`
- Test: `apps/web/backend/tests/test_plugin_config.py`

- [ ] **Step 1: 写失败测试**

创建 `apps/web/backend/tests/test_plugin_config.py`：

```python
"""插件配置存储测试（敏感字段加密、留空保持原值、状态判定）。"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.core.crypto import decrypt_key
from app.plugins.config_store import CONFIG_COLLECTION, PluginConfigStore

SCHEMA = [
    {"key": "base_url", "label": "服务地址", "type": "text", "required": True},
    {"key": "token", "label": "凭证", "type": "password", "secret": True},
]


@pytest.fixture
def fernet_key() -> str:
    """一次性 Fernet key。"""
    return Fernet.generate_key().decode()


async def test_save_and_resolve_roundtrip(store, fernet_key):
    """非敏感字段明文、敏感字段密文落库；resolved 还原扁平配置。"""
    cs = PluginConfigStore(store, fernet_key)
    await cs.save("demo", {"base_url": "http://x", "token": "secret-token"}, SCHEMA)

    doc = await store.get(CONFIG_COLLECTION, "demo")
    assert doc["config"]["base_url"] == "http://x"
    assert "secret-token" not in str(doc["secrets"])  # 密文落库
    assert doc["secrets"]["token"]["encrypted"] is True

    assert await cs.resolved("demo") == {"base_url": "http://x", "token": "secret-token"}


async def test_empty_secret_keeps_previous_value(store, fernet_key):
    """敏感字段留空表示保持原值（前端密码框"留空保持不变"语义）。"""
    cs = PluginConfigStore(store, fernet_key)
    await cs.save("demo", {"base_url": "http://x", "token": "tok-1"}, SCHEMA)
    await cs.save("demo", {"base_url": "http://y", "token": ""}, SCHEMA)

    resolved = await cs.resolved("demo")
    assert resolved["base_url"] == "http://y" and resolved["token"] == "tok-1"


async def test_plaintext_when_no_fernet_key(store):
    """未配 Fernet key 时明文存储（开发模式，与 provider api_key 同口径）。"""
    cs = PluginConfigStore(store, "")
    await cs.save("demo", {"base_url": "http://x", "token": "tok"}, SCHEMA)

    doc = await store.get(CONFIG_COLLECTION, "demo")
    assert doc["secrets"]["token"] == {"value": "tok", "encrypted": False}
    assert await cs.resolved("demo") == {"base_url": "http://x", "token": "tok"}


async def test_installed_and_all_resolved(store, fernet_key):
    """安装状态 = 有配置记录；all_resolved 返回全部已安装插件。"""
    cs = PluginConfigStore(store, fernet_key)
    assert await cs.installed_ids() == []
    await cs.save("demo", {"base_url": "http://x", "token": "t"}, SCHEMA)
    assert await cs.installed_ids() == ["demo"]
    assert (await cs.all_resolved())["demo"]["base_url"] == "http://x"


async def test_resolved_missing_plugin_returns_empty(store, fernet_key):
    """未安装插件的 resolved 返回空 dict（不抛异常）。"""
    assert await PluginConfigStore(store, fernet_key).resolved("nope") == {}


def test_no_plaintext_leak_in_redactable_view(store_shape_check=None):
    """占位：确保 decrypt_key 与 store 的字段名约定一致（见下断言）。"""
    assert decrypt_key("plain", "", False) == "plain"
```

（最后一条测试是廉价的一致性检查；若你觉得冗余可去掉一整条。）

- [ ] **Step 2: 运行测试确认失败**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_plugin_config.py -v
```

预期：`ModuleNotFoundError: No module named 'app.plugins.config_store'`。

- [ ] **Step 3: 实现配置存储**

创建 `apps/web/backend/app/plugins/config_store.py`：

```python
"""插件配置落库（敏感字段 Fernet 加密，留空表示保持原值）。

存储形态（集合 plugin_configs，_id = 插件 id）：
    {
      "_id": "spec_agent",
      "config": {"base_url": "http://..."},          # 非敏感字段明文
      "secrets": {"token": {"value": "<密文>", "encrypted": true}},
      "created_at": ..., "updated_at": ...,
    }
安装状态 = 存在配置记录（jiuwen 式：只认 installed，无全局开关）。
"""
from __future__ import annotations

import time
from typing import Any

from app.core.crypto import decrypt_key, encrypt_key

CONFIG_COLLECTION = "plugin_configs"


class PluginConfigStore:
    """插件配置的读写与解密。"""

    def __init__(self, store: Any, fernet_key: str) -> None:
        """保存依赖。

        Args:
            store: DocumentStore 实例。
            fernet_key: Fernet key（空 = 明文存储，开发模式）。
        """
        self._store = store
        self._fernet_key = fernet_key

    async def get_doc(self, plugin_id: str) -> dict | None:
        """取插件配置原始文档。

        Args:
            plugin_id: 插件 id。

        Returns:
            配置文档；未安装时为 None。
        """
        return await self._store.get(CONFIG_COLLECTION, plugin_id)

    async def installed_ids(self) -> list[str]:
        """全部已安装（有配置记录）的插件 id。"""
        docs = await self._store.list(CONFIG_COLLECTION)
        return sorted(d["_id"] for d in docs)

    async def resolved(self, plugin_id: str) -> dict:
        """解密后的扁平配置（非敏感明文 + 敏感字段解密值）。

        Args:
            plugin_id: 插件 id。

        Returns:
            扁平配置 dict；未安装时为空 dict。
        """
        doc = await self.get_doc(plugin_id)
        if doc is None:
            return {}
        out: dict = dict(doc.get("config") or {})
        for key, item in (doc.get("secrets") or {}).items():
            out[key] = decrypt_key(
                item.get("value", ""), self._fernet_key, bool(item.get("encrypted")))
        return out

    async def all_resolved(self) -> dict[str, dict]:
        """全部已安装插件的解密配置（运行期注入 ctx.extra 用）。

        Returns:
            {插件 id: 扁平配置}。
        """
        return {pid: await self.resolved(pid) for pid in await self.installed_ids()}

    async def save(self, plugin_id: str, values: dict, schema: list[dict]) -> dict:
        """保存配置：非敏感字段覆盖，敏感字段非空才更新（留空保持原值）。

        Args:
            plugin_id: 插件 id（首次保存即视为安装）。
            values: 页面提交的字段值。
            schema: 插件配置 schema（决定哪些字段是敏感的）。

        Returns:
            保存后的原始文档。
        """
        secret_keys = {f["key"] for f in schema if f.get("secret")}
        doc = await self.get_doc(plugin_id) or {}
        config = dict(doc.get("config") or {})
        secrets = dict(doc.get("secrets") or {})
        for key, value in values.items():
            if key in secret_keys:
                if value:
                    stored, encrypted = encrypt_key(str(value), self._fernet_key)
                    secrets[key] = {"value": stored, "encrypted": encrypted}
            else:
                config[key] = value
        body = {"config": config, "secrets": secrets, "updated_at": time.time()}
        if doc:
            updated = await self._store.update(CONFIG_COLLECTION, plugin_id, body)
            return updated or {**doc, **body}
        return await self._store.insert(
            CONFIG_COLLECTION, {"_id": plugin_id, **body, "created_at": time.time()})
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_plugin_config.py -v
```

预期：全部通过（5-6 项）。

- [ ] **Step 5: 提交**

```bash
cd "E:\agent_projects\Synlora" && git add apps/web/backend/app/plugins/config_store.py apps/web/backend/tests/test_plugin_config.py && git commit -m "feat: 插件配置存储（敏感字段加密、留空保持原值）"
```

---

## Task 4: SkillService 支持额外技能根（插件技能不落用户技能目录）

**Files:**
- Modify: `apps/web/backend/app/services/skill_service.py`（`__init__`/`list_skills`/`read_body`/新增 `add_root`）
- Test: `apps/web/backend/tests/test_skill_service.py`

- [ ] **Step 1: 写失败测试**

在 `apps/web/backend/tests/test_skill_service.py` 末尾追加：

```python
def test_extra_roots_are_listed_and_readable(tmp_path):
    """插件技能根：列在技能表里（builtin=True），正文可读，且不可删除。"""
    data_root = tmp_path / "data"
    data_root.mkdir()
    plugin_skills = tmp_path / "plugin" / "skills"
    (plugin_skills / "spec-nmr").mkdir(parents=True)
    (plugin_skills / "spec-nmr" / "SKILL.md").write_text(
        "---\nname: spec-nmr\ndescription: 核磁谱图解析\n---\n正文内容\n",
        encoding="utf-8")

    svc = SkillService(data_root, extra_roots=[plugin_skills])
    skills = {s["name"]: s for s in svc.list_skills()}
    assert skills["spec-nmr"]["builtin"] is True
    assert svc.read_body("spec-nmr") == "正文内容"

    # 插件技能不落用户技能目录，删除只作用于用户目录（返回 False 而非删掉插件技能）
    assert svc.delete_skill("spec-nmr") is False
    assert (plugin_skills / "spec-nmr" / "SKILL.md").exists()


def test_extra_root_added_at_runtime(tmp_path):
    """add_root：运行期安装插件后新技能立即可见。"""
    data_root = tmp_path / "data"
    data_root.mkdir()
    svc = SkillService(data_root)
    assert [s["name"] for s in svc.list_skills()] == []

    plugin_skills = tmp_path / "plugin" / "skills"
    (plugin_skills / "spec-nmr").mkdir(parents=True)
    (plugin_skills / "spec-nmr" / "SKILL.md").write_text(
        "---\nname: spec-nmr\ndescription: 核磁谱图解析\n---\n正文\n", encoding="utf-8")
    svc.add_root(plugin_skills)
    assert [s["name"] for s in svc.list_skills()] == ["spec-nmr"]


def test_user_skill_shadows_plugin_skill(tmp_path):
    """同名时用户技能优先（插件技能不覆盖用户目录里的同名技能）。"""
    data_root = tmp_path / "data"
    user_dir = data_root / "skills" / "spec-nmr"
    user_dir.mkdir(parents=True)
    (user_dir / "SKILL.md").write_text(
        "---\nname: spec-nmr\ndescription: 用户版本\n---\n用户正文\n", encoding="utf-8")
    plugin_skills = tmp_path / "plugin" / "skills"
    (plugin_skills / "spec-nmr").mkdir(parents=True)
    (plugin_skills / "spec-nmr" / "SKILL.md").write_text(
        "---\nname: spec-nmr\ndescription: 插件版本\n---\n插件正文\n", encoding="utf-8")

    svc = SkillService(data_root, extra_roots=[plugin_skills])
    assert svc.read_body("spec-nmr") == "用户正文"
    assert len([s for s in svc.list_skills() if s["name"] == "spec-nmr"]) == 1
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_skill_service.py -v
```

预期：`TypeError: SkillService.__init__() got an unexpected keyword argument 'extra_roots'`。

- [ ] **Step 3: 实现额外技能根**

修改 `apps/web/backend/app/services/skill_service.py`：

1. `__init__` 增加 `extra_roots`：

```python
    def __init__(self, data_root: Path, extra_roots: list[Path] | None = None) -> None:
        """保存数据根与额外技能根。

        Args:
            data_root: 应用数据根目录。
            extra_roots: 额外技能根（插件包的 skills/ 目录；同名时用户目录优先）。
        """
        self._data_root = data_root
        self._extra_roots: list[Path] = list(extra_roots or [])
```

2. 新增 `add_root`（放在 `skills_dir` property 之后）：

```python
    def add_root(self, root: Path) -> None:
        """追加一个额外技能根（插件安装时调用；重复追加幂等）。

        Args:
            root: 技能根目录（其下每个子目录是一个技能）。
        """
        if root not in self._extra_roots:
            self._extra_roots.append(root)
```

3. 抽出「扫描某根目录」的内部方法，`list_skills` 改为遍历用户根 + 额外根（用户优先、同名去重、额外根的技能标记 builtin）：

```python
    def _scan_root(self, root: Path, *, builtin: bool) -> list[dict]:
        """扫描单个技能根目录。

        Args:
            root: 技能根目录（不存在时返回空列表）。
            builtin: 该根的技能是否标记为内置（插件技能为 True，不可删）。

        Returns:
            技能字典列表。
        """
        out: list[dict] = []
        if not root.is_dir():
            return out
        for entry in sorted(root.iterdir()):
            md = entry / "SKILL.md"
            if not md.is_file():
                continue
            try:
                skill = parse_skill_md(md.read_text(encoding="utf-8"))
            except (ValueError, yaml.YAMLError):
                continue
            skill["builtin"] = builtin or skill["name"] in BUILTIN_SKILL_NAMES
            out.append(skill)
        return out

    def list_skills(self) -> list[dict]:
        """扫描全部技能（用户目录 + 额外根；同名用户目录优先）。

        Returns:
            技能字典列表，每项含 `builtin` 标记。
        """
        out = self._scan_root(self.skills_dir, builtin=False)
        seen = {s["name"] for s in out}
        for root in self._extra_roots:
            for skill in self._scan_root(root, builtin=True):
                if skill["name"] not in seen:
                    seen.add(skill["name"])
                    out.append(skill)
        return out
```

4. `read_body` 改为依次在用户目录与额外根中查找：

```python
    def read_body(self, name: str) -> str | None:
        """读技能正文（不含 frontmatter；用户目录优先于额外根）。

        Args:
            name: 技能名（即目录名）。

        Returns:
            正文文本；名字非法、技能不存在或不可解析时返回 None。
        """
        if not NAME_OK.match(name):
            return None
        for root in [self.skills_dir, *self._extra_roots]:
            md = root / name / "SKILL.md"
            if not md.is_file():
                continue
            try:
                return parse_skill_md(md.read_text(encoding="utf-8"))["content"]
            except (ValueError, yaml.YAMLError):
                return None
        return None
```

（`delete_skill` 不改：它只操作 `self.skills_dir`，插件技能天然删不掉。）

- [ ] **Step 4: 运行测试确认通过**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_skill_service.py tests/test_skills_api.py -v
```

预期：全部通过（含既有用例）。

- [ ] **Step 5: 提交**

```bash
cd "E:\agent_projects\Synlora" && git add apps/web/backend/app/services/skill_service.py apps/web/backend/tests/test_skill_service.py && git commit -m "feat: 技能服务支持额外技能根（插件技能不改主框架）"
```

---

## Task 5: PluginService（安装 / 配置 / 上下文注入 / 专家播种）

**Files:**
- Create: `apps/web/backend/app/plugins/service.py`
- Modify: `apps/web/backend/app/plugins/__init__.py`（导出 `PluginService`）
- Test: `apps/web/backend/tests/test_plugin_service.py`

- [ ] **Step 1: 写失败测试**

创建 `apps/web/backend/tests/test_plugin_service.py`：

```python
"""PluginService 编排测试：安装、配置更新、上下文注入、专家播种。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from synlys_harness import ToolRegistry, register_builtin_tools

from app.db.repos import AssistantRepo
from app.plugins.config_store import PluginConfigStore
from app.plugins.loader import scan_plugins
from app.plugins.service import PluginService
from app.services.skill_service import SkillService

TOOLS_SOURCE = '''
"""测试插件工具。"""
from synlys_harness import ToolContext, ToolResult, tool


@tool(name="demo.hello", description="示例工具",
      parameters={"type": "object", "properties": {}})
async def demo_hello(ctx: ToolContext, args: dict) -> ToolResult:
    """示例工具。"""
    return ToolResult(ok=True, content="hi")
'''

MANIFEST = {
    "id": "demo",
    "name": "示例插件",
    "version": "1.0.0",
    "description": "测试插件",
    "tools_module": "tools.py",
    "config_schema": [
        {"key": "base_url", "label": "服务地址", "type": "text", "required": True},
        {"key": "token", "label": "凭证", "type": "password", "secret": True},
    ],
    "skills": ["demo-skill"],
    "expert": {
        "name": "示例专家", "avatar": "🧩", "description": "示例",
        "system_prompt": "你是示例专家。",
        "tool_whitelist": ["demo.hello", "file.read"],
    },
}


@pytest.fixture
def packages(tmp_path) -> dict:
    """造一个含工具/技能/专家模板的插件包并扫描。"""
    pkg_dir = tmp_path / "plugins" / "demo"
    (pkg_dir / "skills" / "demo-skill").mkdir(parents=True)
    (pkg_dir / "plugin.json").write_text(
        json.dumps(MANIFEST, ensure_ascii=False), encoding="utf-8")
    (pkg_dir / "tools.py").write_text(TOOLS_SOURCE, encoding="utf-8")
    (pkg_dir / "skills" / "demo-skill" / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: 示例技能\n---\n正文\n", encoding="utf-8")
    return scan_plugins([tmp_path / "plugins"])


def _service(store, packages, tmp_path) -> tuple[PluginService, ToolRegistry, SkillService]:
    """组装 PluginService 及其依赖。

    Args:
        store: DocumentStore fixture。
        packages: 扫描到的插件包。
        tmp_path: 数据根。

    Returns:
        (service, registry, skill_service)。
    """
    registry = ToolRegistry()
    register_builtin_tools(registry)
    skill_service = SkillService(tmp_path / "data", extra_roots=[])
    config_store = PluginConfigStore(store, Fernet.generate_key().decode())
    service = PluginService(
        registry=registry, config_store=config_store, packages=packages,
        skill_service=skill_service, assistant_repo=AssistantRepo(store))
    return service, registry, skill_service


async def test_startup_registers_nothing_when_not_installed(store, packages, tmp_path):
    """未安装：不注册工具、不挂技能根、上下文为空。"""
    service, registry, skill_service = _service(store, packages, tmp_path)
    await service.startup()
    assert [n for n in registry.names if n.startswith("demo.")] == []
    assert skill_service.list_skills() == []
    assert service.context_extra() == {}
    assert service.list_states()[0]["installed"] is False


async def test_install_registers_tools_skills_expert(store, packages, tmp_path):
    """安装：注册工具、挂技能根、播种专家、缓存配置。"""
    service, registry, skill_service = _service(store, packages, tmp_path)
    await service.startup()

    state = await service.install("demo", {"base_url": "http://x", "token": "t"})
    assert state["installed"] is True and state["missing"] == []
    assert "demo.hello" in registry.names
    assert [s["name"] for s in skill_service.list_skills()] == ["demo-skill"]
    assert service.context_extra() == {"demo": {"base_url": "http://x", "token": "t"}}

    expert = await AssistantRepo(store).get("asst-plugin-demo")
    assert expert is not None and expert["builtin"] is True
    assert expert["plugin_id"] == "demo"
    assert "demo.hello" in expert["tool_whitelist"]


async def test_install_missing_required_raises(store, packages, tmp_path):
    """缺必填字段：抛 ValueError（由 API 层转 422），且不产生安装记录。"""
    service, _, _ = _service(store, packages, tmp_path)
    await service.startup()
    with pytest.raises(ValueError, match="base_url"):
        await service.install("demo", {"token": "t"})
    assert service.list_states()[0]["installed"] is False


async def test_install_unknown_plugin_raises_keyerror(store, packages, tmp_path):
    """未知插件 id 抛 KeyError（由 API 层转 404）。"""
    service, _, _ = _service(store, packages, tmp_path)
    await service.startup()
    with pytest.raises(KeyError):
        await service.install("nope", {})


async def test_update_config_refreshes_context(store, packages, tmp_path):
    """更新配置：上下文缓存同步刷新，敏感字段留空保持原值。"""
    service, _, _ = _service(store, packages, tmp_path)
    await service.startup()
    await service.install("demo", {"base_url": "http://x", "token": "tok-1"})

    await service.update_config("demo", {"base_url": "http://y", "token": ""})
    assert service.context_extra() == {"demo": {"base_url": "http://y", "token": "tok-1"}}


async def test_startup_restores_installed_plugin(store, packages, tmp_path):
    """重启恢复：已安装插件在 startup 时重新注册工具/技能/上下文。"""
    service, _, _ = _service(store, packages, tmp_path)
    await service.startup()
    await service.install("demo", {"base_url": "http://x"})

    service2, registry2, skill_service2 = _service(store, packages, tmp_path)
    await service2.startup()
    assert "demo.hello" in registry2.names
    assert [s["name"] for s in skill_service2.list_skills()] == ["demo-skill"]
    assert service2.context_extra()["demo"]["base_url"] == "http://x"


async def test_list_states_excludes_secret_values(store, packages, tmp_path):
    """状态回报不含敏感值（只给是否已配置），避免密钥回传前端。"""
    service, _, _ = _service(store, packages, tmp_path)
    await service.startup()
    await service.install("demo", {"base_url": "http://x", "token": "super-secret"})

    state = service.list_states()[0]
    assert "super-secret" not in json.dumps(state, ensure_ascii=False)
    assert state["secrets_set"] == {"token": True}
    assert state["config"] == {"base_url": "http://x"}
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_plugin_service.py -v
```

预期：`ModuleNotFoundError: No module named 'app.plugins.service'`。

- [ ] **Step 3: 实现 PluginService**

创建 `apps/web/backend/app/plugins/service.py`：

```python
"""插件编排：安装、配置、运行上下文与专家播种。

宿主为每个已安装插件：
1. 注册其工具到共享注册表（未安装 = 工具对 LLM 不可见）；
2. 挂上其技能根（技能留在插件目录，不复制进用户技能目录）；
3. 把解密配置缓存进内存，运行期按命名空间注入 ctx.extra["plugins"]；
4. 首装时按 manifest 的专家模板播种一个助手（内置、带 plugin_id 溯源）。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.plugins.loader import PluginPackage, load_plugin_tools

if TYPE_CHECKING:
    from synlys_harness import ToolRegistry

    from app.plugins.config_store import PluginConfigStore

logger = logging.getLogger(__name__)

EXPERT_ID_PREFIX = "asst-plugin-"


class PluginService:
    """插件安装状态与运行期装配。"""

    def __init__(self, registry: "ToolRegistry", config_store: "PluginConfigStore",
                 packages: dict[str, PluginPackage], skill_service: Any,
                 assistant_repo: Any) -> None:
        """保存依赖。

        Args:
            registry: 共享工具注册表（插件工具注册于此）。
            config_store: 插件配置存储（安装状态来源）。
            packages: 扫描到的插件包（{id: PluginPackage}）。
            skill_service: 技能服务（挂插件技能根）。
            assistant_repo: 助手 repo（播种专家）。
        """
        self._registry = registry
        self._config_store = config_store
        self._packages = packages
        self._skill_service = skill_service
        self._assistant_repo = assistant_repo
        self._configs: dict[str, dict] = {}

    @staticmethod
    def expert_id(plugin_id: str) -> str:
        """插件播种专家的助手 id。

        Args:
            plugin_id: 插件 id。

        Returns:
            助手文档 id（asst-plugin-<id>）。
        """
        return f"{EXPERT_ID_PREFIX}{plugin_id}"

    def package(self, plugin_id: str) -> PluginPackage:
        """取插件包。

        Args:
            plugin_id: 插件 id。

        Returns:
            插件包。

        Raises:
            KeyError: 插件不存在。
        """
        if plugin_id not in self._packages:
            raise KeyError(f"插件不存在: {plugin_id}")
        return self._packages[plugin_id]

    async def startup(self) -> None:
        """启动装配：恢复已安装插件的工具、技能根、配置缓存与专家。"""
        for plugin_id in await self._config_store.installed_ids():
            package = self._packages.get(plugin_id)
            if package is None:
                logger.warning("插件配置存在但插件包缺失，已忽略: %s", plugin_id)
                continue
            self._attach(package)
            await self._seed_expert(package)  # 自愈：专家被误删则重启补种
        # 只缓存有插件包的插件配置：包缺失时其工具未注册，注入配置无意义（且是无谓的凭证暴露面）
        self._configs = {
            pid: cfg for pid, cfg in (await self._config_store.all_resolved()).items()
            if pid in self._packages
        }

    def _attach(self, package: PluginPackage) -> None:
        """挂载插件资源：注册工具（幂等）+ 挂技能根。

        Args:
            package: 插件包。
        """
        for fn in load_plugin_tools(package):
            name = fn.__tool_definition__.name
            if self._registry.find(name) is None:
                self._registry.register(fn)
        if package.skills_root is not None:
            self._skill_service.add_root(package.skills_root)

    def _validate(self, package: PluginPackage, values: dict) -> None:
        """校验必填配置。

        Args:
            package: 插件包。
            values: 提交的配置值。

        Raises:
            ValueError: 存在未填的必填字段（消息含缺失字段名）。
        """
        missing = [
            f["key"] for f in package.config_schema
            if f.get("required") and not str(values.get(f["key"]) or "").strip()
        ]
        if missing:
            raise ValueError(f"缺少必填配置: {', '.join(missing)}")

    async def install(self, plugin_id: str, values: dict) -> dict:
        """安装插件：校验 → 存配置 → 挂资源 → 播种专家。

        Args:
            plugin_id: 插件 id。
            values: 页面提交的配置值。

        Returns:
            安装后的状态（不含敏感值）。

        Raises:
            KeyError: 插件不存在。
            ValueError: 必填字段缺失。
        """
        package = self.package(plugin_id)
        self._validate(package, values)
        await self._config_store.save(plugin_id, values, package.config_schema)
        self._attach(package)
        self._configs[plugin_id] = await self._config_store.resolved(plugin_id)
        await self._seed_expert(package)
        return self.state(plugin_id)

    async def update_config(self, plugin_id: str, values: dict) -> dict:
        """更新已安装插件的配置。

        Args:
            plugin_id: 插件 id。
            values: 页面提交的配置值（敏感字段留空 = 保持原值）。

        Returns:
            更新后的状态。

        Raises:
            KeyError: 插件不存在。
            ValueError: 插件未安装，或必填字段缺失。
        """
        package = self.package(plugin_id)
        if await self._config_store.get_doc(plugin_id) is None:
            raise ValueError(f"插件未安装: {plugin_id}")
        current = await self._config_store.resolved(plugin_id)
        merged = {**current, **{k: v for k, v in values.items() if v}}
        self._validate(package, merged)
        await self._config_store.save(plugin_id, values, package.config_schema)
        self._configs[plugin_id] = await self._config_store.resolved(plugin_id)
        return self.state(plugin_id)

    async def _seed_expert(self, package: PluginPackage) -> None:
        """按 manifest 专家模板播种助手（已存在则不动）。

        Args:
            package: 插件包。
        """
        if not package.expert:
            return
        doc_id = self.expert_id(package.id)
        if await self._assistant_repo.get(doc_id) is not None:
            return
        expert = package.expert
        await self._assistant_repo.create({
            "_id": doc_id,
            "name": expert.get("name") or f"{package.name}专家",
            "avatar": expert.get("avatar") or "🧩",
            "description": expert.get("description") or package.description,
            "system_prompt": expert.get("system_prompt") or "",
            "tool_whitelist": list(expert.get("tool_whitelist") or []),
            "model_provider_id": None,
            "knowledge_base_ids": list(expert.get("knowledge_base_ids") or []),
            "builtin": True,
            "plugin_id": package.id,
        })

    def context_extra(self) -> dict[str, dict]:
        """已安装插件的解密配置（运行期注入 ctx.extra["plugins"]）。

        Returns:
            {插件 id: 扁平配置} 的副本。
        """
        return {pid: dict(cfg) for pid, cfg in self._configs.items()}

    def state(self, plugin_id: str) -> dict:
        """单个插件的状态（不含敏感值）。

        Args:
            plugin_id: 插件 id。

        Returns:
            含 id/name/installed/configured/missing/config/secrets_set 的字典。
        """
        package = self._packages[plugin_id]
        config = self._configs.get(plugin_id, {})
        secrets_set = {
            f["key"]: bool(config.get(f["key"]))
            for f in package.config_schema if f.get("secret")
        }
        missing = [
            f["key"] for f in package.config_schema if f.get("required") and not config.get(f["key"])
        ]
        return {
            "id": package.id,
            "name": package.name,
            "version": package.version,
            "description": package.description,
            "config_schema": package.config_schema,
            "installed": plugin_id in self._configs,
            "configured": plugin_id in self._configs and not missing,
            "missing": missing,
            "config": {k: v for k, v in config.items() if not _is_secret(package, k)},
            "secrets_set": secrets_set,
        }

    def list_states(self) -> list[dict]:
        """全部可用插件的状态（按 id 排序）。

        Returns:
            状态字典列表。
        """
        return [self.state(pid) for pid in sorted(self._packages)]


def _is_secret(package: PluginPackage, key: str) -> bool:
    """判断字段在插件 schema 里是否标记为敏感。

    Args:
        package: 插件包。
        key: 字段名。

    Returns:
        True 表示敏感字段（不应回传前端）。
    """
    return any(f["key"] == key and f.get("secret") for f in package.config_schema)
```

同时更新 `apps/web/backend/app/plugins/__init__.py` 末尾追加导出：

```python
from app.plugins.config_store import PluginConfigStore
from app.plugins.loader import PluginPackage, load_plugin_tools, plugin_roots, scan_plugins
from app.plugins.service import PluginService

__all__ = [
    "PluginConfigStore", "PluginPackage", "PluginService",
    "load_plugin_tools", "plugin_roots", "scan_plugins",
]
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_plugin_service.py -v
```

预期：7 passed。

- [ ] **Step 5: 提交**

```bash
cd "E:\agent_projects\Synlora" && git add apps/web/backend/app/plugins/ apps/web/backend/tests/test_plugin_service.py && git commit -m "feat: PluginService 编排（安装/配置/上下文注入/专家播种）"
```

---

## Task 6: 插件包 spec_agent（manifest + 工具 + 技能）

**Files:**
- Create: `apps/web/backend/plugins/spec_agent/plugin.json`
- Create: `apps/web/backend/plugins/spec_agent/tools.py`
- Create: `apps/web/backend/plugins/spec_agent/skills/spec-nmr/SKILL.md`
- Test: `apps/web/backend/tests/test_spec_agent_plugin.py`

**上游契约（已核对 Spec_Agent 源码，勿改字段名）：**

| 端点 | 请求体 |
|---|---|
| `POST {base}/api/v1/nmrserver/forward` | `{"smiles_input": str}` |
| `POST {base}/api/v1/nmrserver/reverse` | `{"h_shifts_input", "h_split_input", "c_shifts_input", "formula", "allowed_elements", "candidates"}`（均字符串，可空） |
| `POST {base}/api/v1/nmrserver/search` | `{"h_shifts_input", "h_split_input", "c_shifts_input", "num_search": 500(10~10000), "topk": 10(1~100), "allowed_elements": "C,H,N,O"}` |

响应 `{"code": 0, "message": "ok", "data": {"items": [...]}}`；`code != 0` 为业务错误；上游超时 400/502/504；**单次最长阻塞约 350s**。鉴权 `Authorization: Bearer <token>`（Spec_Agent `AUTH_ENABLED=false` 时不校验，故 token 可为空 = 不发头）。

- [ ] **Step 1: 写失败测试**

创建 `apps/web/backend/tests/test_spec_agent_plugin.py`：

```python
"""Spec_Agent 插件包测试：manifest、工具收集、工具层行为（MockTransport 打桩）。"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from synlys_harness import ToolContext

from app.plugins.loader import load_plugin_tools, scan_plugins

REPO_PLUGINS = Path(__file__).resolve().parents[1] / "plugins"


def _load_tools() -> dict:
    """从仓库插件目录加载 spec_agent 工具（{工具名: 函数}）。

    Returns:
        工具名到函数的映射。
    """
    packages = scan_plugins([REPO_PLUGINS])
    tools = load_plugin_tools(packages["spec_agent"])
    return {t.__tool_definition__.name: t for t in tools}


def test_manifest_is_valid_and_complete():
    """manifest 可解析，schema/技能/专家模板齐备。"""
    pkg = scan_plugins([REPO_PLUGINS])["spec_agent"]
    assert pkg.name and pkg.version == "1.0.0"
    assert [f["key"] for f in pkg.config_schema] == ["base_url", "token"]
    assert pkg.config_schema[0]["required"] is True
    assert pkg.config_schema[1]["secret"] is True
    assert pkg.skills == ["spec-nmr"]
    assert pkg.skills_root is not None
    assert pkg.expert["name"] == "谱图解析专家"
    assert "spec.nmr.forward" in pkg.expert["tool_whitelist"]


def test_tools_registered_names():
    """插件导出三个工具，工具级超时 380s（> 上游 350s）。"""
    tools = _load_tools()
    assert sorted(tools) == ["spec.nmr.forward", "spec.nmr.reverse", "spec.nmr.search"]
    assert tools["spec.nmr.forward"].__tool_definition__.timeout_s == 380.0


def _ctx(config: dict | None) -> ToolContext:
    """构造带插件命名空间配置的工具上下文。

    Args:
        config: spec_agent 插件配置（None = 未配置）。

    Returns:
        ToolContext。
    """
    plugins = {"spec_agent": config} if config is not None else {}
    return ToolContext(user_id="u", run_id="r", workspace_root=None,
                       extra={"plugins": plugins})


def _patch_transport(monkeypatch, handler) -> None:
    """把插件的 HTTP 客户端换成 MockTransport 版本。"""
    tools = _load_tools()
    module = __import__(tools["spec.nmr.forward"].__module__, fromlist=["_make_client"])
    monkeypatch.setattr(
        module, "_make_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=1.0),
    )


async def test_unconfigured_returns_clear_error():
    """未配置（插件未安装）时不出请求，返回明确错误。"""
    tools = _load_tools()
    r = await tools["spec.nmr.forward"](_ctx(None), {"smiles_input": "CCO"})
    assert not r.ok and r.error == "spec_agent_unconfigured"
    assert "插件" in r.content


async def test_forward_success(monkeypatch):
    """正向预测：路径/认证头/请求体正确，items 逐条返回。"""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/nmrserver/forward"
        assert request.headers["authorization"] == "Bearer tok"
        assert json.loads(request.content) == {"smiles_input": "CCO"}
        return httpx.Response(200, json={
            "code": 0, "message": "ok",
            "data": {"items": [{"smiles": "CCO", "c_shifts": [58.0]}]}})

    _patch_transport(monkeypatch, handler)
    tools = _load_tools()
    r = await tools["spec.nmr.forward"](
        _ctx({"base_url": "http://spec.local", "token": "tok"}), {"smiles_input": "CCO"})
    assert r.ok and "CCO" in r.content and r.data["items"] == 1


async def test_empty_token_sends_no_auth_header(monkeypatch):
    """token 留空 = 不发认证头（适配 Spec_Agent AUTH_ENABLED=false）。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["has_auth"] = "authorization" in request.headers
        return httpx.Response(200, json={"code": 0, "message": "ok", "data": {"items": []}})

    _patch_transport(monkeypatch, handler)
    tools = _load_tools()
    r = await tools["spec.nmr.forward"](_ctx({"base_url": "http://spec.local"}), {"smiles_input": "CCO"})
    assert r.ok and seen["has_auth"] is False


async def test_upstream_http_error(monkeypatch):
    """上游 5xx 转 ok=False。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(504, text="NMRServer 请求超时")

    _patch_transport(monkeypatch, handler)
    tools = _load_tools()
    r = await tools["spec.nmr.reverse"](_ctx({"base_url": "http://x"}), {"c_shifts_input": "20,30"})
    assert not r.ok and r.error == "http_error" and "504" in r.content


async def test_upstream_business_error(monkeypatch):
    """code != 0 转 ok=False 且带上游 message。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 5001, "message": "SMILES 非法", "data": None})

    _patch_transport(monkeypatch, handler)
    tools = _load_tools()
    r = await tools["spec.nmr.forward"](_ctx({"base_url": "http://x"}), {"smiles_input": "bad"})
    assert not r.ok and r.error == "upstream_error" and "SMILES 非法" in r.content


async def test_search_payload_defaults(monkeypatch):
    """检索：未传参时按上游约束填默认值。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"code": 0, "message": "ok", "data": {"items": []}})

    _patch_transport(monkeypatch, handler)
    tools = _load_tools()
    r = await tools["spec.nmr.search"](_ctx({"base_url": "http://x"}), {"c_shifts_input": "20,30"})
    assert r.ok and r.content == "（未返回结果）"
    assert seen["num_search"] == 500 and seen["topk"] == 10
    assert seen["allowed_elements"] == "C,H,N,O" and seen["h_shifts_input"] == ""


def test_skill_file_parses():
    """插件自带技能 SKILL.md 可被技能解析器解析。"""
    from app.services.skill_service import parse_skill_md

    md = REPO_PLUGINS / "spec_agent" / "skills" / "spec-nmr" / "SKILL.md"
    skill = parse_skill_md(md.read_text(encoding="utf-8"))
    assert skill["name"] == "spec-nmr" and "核磁" in skill["description"]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_spec_agent_plugin.py -v
```

预期：`KeyError: 'spec_agent'`（插件包目录尚未创建）。

- [ ] **Step 3: 写 manifest**

创建 `apps/web/backend/plugins/spec_agent/plugin.json`：

```json
{
  "id": "spec_agent",
  "name": "Spec_Agent 谱图解析",
  "version": "1.0.0",
  "description": "核磁（NMR）谱图解析：正向预测（结构→谱）、反向预测（谱→结构）与谱图数据库检索",
  "author": "AI⁴MS",
  "tools_module": "tools.py",
  "config_schema": [
    {
      "key": "base_url",
      "label": "服务地址",
      "type": "text",
      "required": true,
      "placeholder": "http://10.26.15.93:8001",
      "description": "Spec_Agent 服务地址（含协议与端口）"
    },
    {
      "key": "token",
      "label": "访问凭证",
      "type": "password",
      "secret": true,
      "required": false,
      "placeholder": "服务端未开启鉴权时可留空",
      "description": "调用 Spec_Agent 的 Bearer token；留空表示不发送认证头"
    }
  ],
  "skills": ["spec-nmr"],
  "expert": {
    "name": "谱图解析专家",
    "avatar": "🔬",
    "description": "核磁谱图解析：正向/反向预测与谱图数据库检索",
    "system_prompt": "你是谱图解析专家，专注核磁（NMR）谱图与分子结构的双向解析。给定结构时用正向预测核对谱图，给定实测谱峰时用反向预测或数据库检索推断候选结构。给出结论时说明依据与置信度；多候选要排序并说明理由；输入不足或谱峰与候选明显不符时如实说明，不要臆造。",
    "tool_whitelist": [
      "spec.nmr.forward",
      "spec.nmr.reverse",
      "spec.nmr.search",
      "file.read",
      "file.write",
      "file.list",
      "python.run",
      "knowledge.search",
      "ask_user",
      "file.send"
    ]
  }
}
```

- [ ] **Step 4: 写工具模块**

创建 `apps/web/backend/plugins/spec_agent/tools.py`：

```python
"""Spec_Agent 核磁预测工具（插件包自带，宿主动态加载）。

配置由宿主按命名空间注入 ctx.extra["plugins"]["spec_agent"]（见 plugin.json 的
config_schema），本模块不认识任何宿主全局配置——插件只依赖宿主给出的这个通用通道。
上游契约见 Spec_Agent `backend/app/api/v1/endpoints/nmr_server.py`。
"""
from __future__ import annotations

import json

import httpx

from synlys_harness import ToolContext, ToolResult, tool

PLUGIN_ID = "spec_agent"
REQUEST_TIMEOUT_S = 350.0   # 上游 model 推理 read_timeout
TOOL_TIMEOUT_S = 380.0      # 工具级超时必须大于 HTTP 超时：让 HTTP 层先报错


def _config(ctx: ToolContext) -> dict:
    """取本插件在运行上下文中的配置（宿主按插件 id 命名空间注入）。

    Args:
        ctx: 工具上下文。

    Returns:
        插件配置 dict（未安装/未配置时为空 dict）。
    """
    return (ctx.extra.get("plugins") or {}).get(PLUGIN_ID) or {}


def _make_client() -> httpx.AsyncClient:
    """创建 HTTP 客户端（测试经 monkeypatch 注入 MockTransport）。

    Returns:
        配好超时的 httpx 异步客户端。
    """
    return httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S)


async def _call_nmrserver(ctx: ToolContext, path: str, payload: dict) -> ToolResult:
    """调用 NMRServer 端点并归一化为工具结果。

    Args:
        ctx: 工具上下文（取插件配置）。
        path: 端点路径段（forward/reverse/search）。
        payload: 请求体（字段名与上游 pydantic 模型一致）。

    Returns:
        ToolResult：content 为逐条 JSON（items）或"（未返回结果）"；失败时
        error 为 spec_agent_unconfigured / http_error / upstream_error。
    """
    config = _config(ctx)
    base_url = str(config.get("base_url") or "").rstrip("/")
    if not base_url:
        return ToolResult(
            ok=False,
            content="谱图解析插件未配置服务地址（请在管理后台的插件页安装并填写）",
            error="spec_agent_unconfigured",
        )
    headers: dict[str, str] = {}
    token = str(config.get("token") or "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with _make_client() as client:
            resp = await client.post(
                f"{base_url}/api/v1/nmrserver/{path}", headers=headers, json=payload)
    except httpx.HTTPError as exc:
        return ToolResult(ok=False, content=f"谱图服务请求失败: {exc}", error="http_error")
    if resp.status_code != 200:
        return ToolResult(
            ok=False,
            content=f"谱图服务返回 {resp.status_code}: {resp.text[:300]}",
            error="http_error",
        )
    try:
        body = resp.json() or {}
    except ValueError:
        return ToolResult(
            ok=False, content=f"谱图服务返回非 JSON: {resp.text[:200]}",
            error="upstream_error")
    if body.get("code") != 0:
        return ToolResult(
            ok=False,
            content=f"谱图服务错误: {body.get('message') or body.get('code')}",
            error="upstream_error",
        )
    data = body.get("data") or {}
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        return ToolResult(ok=True, content="（未返回结果）", data={"items": 0})
    return ToolResult(
        ok=True,
        content="\n".join(json.dumps(item, ensure_ascii=False) for item in items),
        data={"items": len(items)},
    )


@tool(
    name="spec.nmr.forward",
    description=(
        "核磁正向预测：给定分子 SMILES（可多行，每行一个），预测其 13C/1H 化学位移。"
        "用于验证「推测结构理论上应出什么谱」。模型推理较慢，单次可能数分钟，"
        "调用后请耐心等待，不要连续重复提交同一请求。"
    ),
    parameters={"type": "object", "properties": {
        "smiles_input": {"type": "string", "description": "多行 SMILES，每行一个分子"},
    }, "required": ["smiles_input"]},
    timeout_s=TOOL_TIMEOUT_S,
)
async def spec_nmr_forward(ctx: ToolContext, args: dict) -> ToolResult:
    """正向核磁预测（SMILES → 化学位移）。

    Args:
        ctx: 工具上下文。
        args: 含 smiles_input。

    Returns:
        ToolResult（每行一个分子的预测结果 JSON）。
    """
    return await _call_nmrserver(ctx, "forward", {"smiles_input": args["smiles_input"]})


@tool(
    name="spec.nmr.reverse",
    description=(
        "核磁反向预测：给定实测化学位移（碳谱/氢谱，逗号分隔的数值串），推断可能的"
        "分子结构候选。可给分子式/允许元素/候选分子约束提高命中率。"
        "模型推理较慢，单次可能数分钟，不要连续重复提交。"
    ),
    parameters={"type": "object", "properties": {
        "c_shifts_input": {"type": "string", "description": "碳谱化学位移，逗号分隔"},
        "h_shifts_input": {"type": "string", "description": "氢谱化学位移，逗号分隔"},
        "h_split_input": {"type": "string", "description": "氢谱峰裂分类型（与氢谱位移对应）"},
        "formula": {"type": "string", "description": "分子式约束（可选）"},
        "allowed_elements": {"type": "string", "description": "允许元素，逗号分隔（可选）"},
        "candidates": {"type": "string", "description": "候选分子 SMILES（可选）"},
    }, "required": []},
    timeout_s=TOOL_TIMEOUT_S,
)
async def spec_nmr_reverse(ctx: ToolContext, args: dict) -> ToolResult:
    """反向核磁预测（化学位移 → 结构候选）。

    Args:
        ctx: 工具上下文。
        args: 含各可选位移/约束字段。

    Returns:
        ToolResult（候选结构 JSON）。
    """
    return await _call_nmrserver(ctx, "reverse", {
        "h_shifts_input": str(args.get("h_shifts_input", "")),
        "h_split_input": str(args.get("h_split_input", "")),
        "c_shifts_input": str(args.get("c_shifts_input", "")),
        "formula": str(args.get("formula", "")),
        "allowed_elements": str(args.get("allowed_elements", "")),
        "candidates": str(args.get("candidates", "")),
    })


@tool(
    name="spec.nmr.search",
    description=(
        "核磁数据库检索：给定实测化学位移，在谱图数据库中检索最接近的化合物"
        "（返回候选与匹配信息）。适合「已知谱峰，想找库里最像的分子」。"
    ),
    parameters={"type": "object", "properties": {
        "c_shifts_input": {"type": "string", "description": "碳谱化学位移，逗号分隔"},
        "h_shifts_input": {"type": "string", "description": "氢谱化学位移，逗号分隔"},
        "h_split_input": {"type": "string", "description": "氢谱峰裂分类型"},
        "num_search": {"type": "integer", "default": 500,
                       "description": "候选搜索数量（10-10000）"},
        "topk": {"type": "integer", "default": 10, "description": "返回条数（1-100）"},
        "allowed_elements": {"type": "string", "default": "C,H,N,O",
                             "description": "允许元素，逗号分隔"},
    }, "required": []},
    timeout_s=TOOL_TIMEOUT_S,
)
async def spec_nmr_search(ctx: ToolContext, args: dict) -> ToolResult:
    """核磁数据库检索（化学位移 → 库内匹配）。

    Args:
        ctx: 工具上下文。
        args: 含位移输入与检索参数。

    Returns:
        ToolResult（库内候选 JSON）。
    """
    return await _call_nmrserver(ctx, "search", {
        "h_shifts_input": str(args.get("h_shifts_input", "")),
        "h_split_input": str(args.get("h_split_input", "")),
        "c_shifts_input": str(args.get("c_shifts_input", "")),
        "num_search": int(args.get("num_search", 500)),
        "topk": int(args.get("topk", 10)),
        "allowed_elements": str(args.get("allowed_elements", "C,H,N,O")),
    })
```

- [ ] **Step 5: 写插件技能**

创建 `apps/web/backend/plugins/spec_agent/skills/spec-nmr/SKILL.md`：

```markdown
---
name: spec-nmr
description: 核磁（NMR）谱图解析（正向预测结构→谱、反向预测谱→结构、数据库检索）。用户说"解析这个核磁谱""这个结构NMR什么样""根据碳谱推测结构"时使用。
version: "1.0"
author: AI⁴MS
tags:
  - 谱图
  - 核磁
---

# 核磁谱图解析

## 目标
用核磁预测工具把「结构」与「谱图」双向打通：给定结构预测谱图、给定实测谱峰推断结构候选、或在谱图库里检索最接近的已知化合物。

## 工具
- `spec.nmr.forward`：SMILES（可多行）→ 预测 13C/1H 化学位移
- `spec.nmr.reverse`：实测化学位移（碳谱/氢谱，逗号分隔数值）→ 结构候选
- `spec.nmr.search`：实测化学位移 → 谱图数据库内匹配候选

三个工具都是**模型推理**，单次可能数分钟，不要连续重复提交同一请求；超时或失败时先看错误信息再决定是否重试。

## 工作流
1. 先判断诉求：验证已有结构（→ forward）、从谱峰推断结构（→ reverse）、还是找库内已知物（→ search）
2. 整理输入：
   - 结构：取 SMILES（工作区里的结构文件用 `file.read` 读，必要时用 `python.run` 解析）
   - 谱峰：碳谱/氢谱位移数值（逗号分隔），有裂分信息一并带上
   - 约束：分子式、允许元素、候选分子（有则显著提高 reverse 命中率）
3. 调用对应工具，**一次只调一个**；结果作为候选而非定论
4. 需要进一步筛选时，可用 `spec.nmr.forward` 对候选结构做正向验证，比对预测位移与实测位移
5. 结论写入工作区 `output/`（表格用 `python.run` 生成 .csv/.xlsx），再用 `file.send` 交付

## 决策规则
- 输入不完整（既无结构也无谱峰数值）时先用 `ask_user` 问清楚，不要凭空编造谱峰或结构
- 位移数值保留一位小数；不同溶剂/仪器会有偏差，说明时提醒用户
- reverse/search 返回多个候选时，**列出前几个并给出排序依据**（匹配度/约束满足情况），不要只给一个结论
- 谱峰与候选结构明显不符时，直说不匹配，不要强行解释
- 需要文献佐证时配合 `knowledge.search` 检索方法学依据

## 输出要求
- 结论先行：是/不是某结构、候选排序、匹配度
- 附关键数字（实测位移 vs 预测位移对照）
- 产物给工作区相对路径（`output/xxx`），并说明数据来源（工具名 + 输入参数）
```

- [ ] **Step 6: 运行测试确认通过**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_spec_agent_plugin.py -v
```

预期：9 passed。

- [ ] **Step 7: 提交**

```bash
cd "E:\agent_projects\Synlora" && git add apps/web/backend/plugins/ apps/web/backend/tests/test_spec_agent_plugin.py && git commit -m "feat: Spec_Agent 插件包（manifest + 核磁三件套工具 + spec-nmr 技能）"
```

---

## Task 7: 插件管理 API + lifespan 装配

**Files:**
- Create: `apps/web/backend/app/plugins/api.py`
- Modify: `apps/web/backend/app/main.py`（lifespan 装配插件框架 + include 路由）
- Test: `apps/web/backend/tests/test_plugins_api.py`

- [ ] **Step 1: 写失败测试**

创建 `apps/web/backend/tests/test_plugins_api.py`：

```python
"""插件管理 API 测试（列表 / 安装 / 更新配置 / 权限 / 密钥不回传）。"""
from __future__ import annotations

import pytest

from app.services.tool_registry import REGISTRY


@pytest.fixture(autouse=True)
async def clean_plugin_state(app):
    """用例结束后清理：注销插件工具、删配置与播种专家。

    共享注册表是进程级单例，安装测试注册进去的 spec.* 工具必须回收，
    否则会污染后续用例（如注册表为空断言）。
    """
    yield
    for name in list(REGISTRY.names):
        if name.startswith("spec."):
            REGISTRY.unregister(name)
    await app.state.store.delete("plugin_configs", "spec_agent")
    await app.state.store.delete("assistants", "asst-plugin-spec_agent")
    app.state.plugin_service._configs.pop("spec_agent", None)


async def test_list_plugins_requires_admin(client, user_headers, admin_headers):
    """列表仅管理员可见（普通用户 403）。"""
    assert (await client.get("/api/v1/plugins", headers=user_headers)).status_code == 403
    resp = await client.get("/api/v1/plugins", headers=admin_headers)
    assert resp.status_code == 200
    items = {p["id"]: p for p in resp.json()}
    assert "spec_agent" in items
    spec = items["spec_agent"]
    assert spec["installed"] is False and spec["configured"] is False
    assert [f["key"] for f in spec["config_schema"]] == ["base_url", "token"]
    assert spec["config_schema"][1]["secret"] is True


async def test_install_requires_base_url(client, admin_headers):
    """缺必填字段 422（消息含字段名），且不产生安装记录。"""
    resp = await client.post("/api/v1/plugins/spec_agent/install",
                             json={"config": {"token": "t"}}, headers=admin_headers)
    assert resp.status_code == 422 and "base_url" in resp.json()["detail"]

    items = {p["id"]: p for p in (await client.get(
        "/api/v1/plugins", headers=admin_headers)).json()}
    assert items["spec_agent"]["installed"] is False


async def test_install_unknown_plugin_404(client, admin_headers):
    """未知插件 id → 404。"""
    resp = await client.post("/api/v1/plugins/nope/install",
                             json={"config": {}}, headers=admin_headers)
    assert resp.status_code == 404


async def test_install_registers_tools_and_never_returns_secret(client, admin_headers):
    """安装成功：工具进注册表、专家播种、状态里不回传密钥明文。"""
    resp = await client.post(
        "/api/v1/plugins/spec_agent/install",
        json={"config": {"base_url": "http://spec.local", "token": "super-secret"}},
        headers=admin_headers)
    assert resp.status_code == 201
    state = resp.json()
    assert state["installed"] is True and state["configured"] is True
    assert "super-secret" not in str(state)
    assert state["secrets_set"] == {"token": True}
    assert state["config"] == {"base_url": "http://spec.local"}

    assert "spec.nmr.forward" in REGISTRY.names
    assert "spec.nmr.reverse" in REGISTRY.names
    assert "spec.nmr.search" in REGISTRY.names

    # 播种专家（内置、可被助手列表看到）
    experts = await client.get("/api/v1/assistants", headers=admin_headers)
    names = [a["name"] for a in experts.json()]
    assert "谱图解析专家" in names

    # 插件技能出现在技能列表里（builtin 标记）
    skills = await client.get("/api/v1/skills", headers=admin_headers)
    spec_skill = next(s for s in skills.json() if s["name"] == "spec-nmr")
    assert spec_skill["builtin"] is True


async def test_update_config_keeps_secret_when_blank(app, client, admin_headers):
    """更新配置：敏感字段留空保持原值，非敏感字段可改。"""
    await client.post("/api/v1/plugins/spec_agent/install",
                      json={"config": {"base_url": "http://a", "token": "tok-1"}},
                      headers=admin_headers)
    resp = await client.put("/api/v1/plugins/spec_agent/config",
                            json={"config": {"base_url": "http://b", "token": ""}},
                            headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["config"]["base_url"] == "http://b"
    assert resp.json()["secrets_set"] == {"token": True}
    assert app.state.plugin_service.context_extra() == {
        "spec_agent": {"base_url": "http://b", "token": "tok-1"}}


async def test_put_config_before_install_409(client, admin_headers):
    """未安装就更新配置 → 409。"""
    resp = await client.put("/api/v1/plugins/spec_agent/config",
                            json={"config": {"base_url": "http://a"}},
                            headers=admin_headers)
    assert resp.status_code == 409
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_plugins_api.py -v
```

预期：404（路由不存在）。

- [ ] **Step 3: 实现 API**

创建 `apps/web/backend/app/plugins/api.py`：

```python
"""插件管理 API：列表、安装、更新配置（均限管理员）。

安全约定：状态回报永不包含敏感字段明文（只给 secrets_set 布尔映射），
与 DSH 的 settings redact 同口径。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.deps import require_admin

router = APIRouter(prefix="/api/v1/plugins", tags=["plugins"])


def get_plugin_service(request: Request):
    """从 app.state 取 PluginService。

    Args:
        request: FastAPI 请求。

    Returns:
        PluginService 实例。

    Raises:
        HTTPException: 未就绪（503）。
    """
    service = getattr(request.app.state, "plugin_service", None)
    if service is None:
        raise HTTPException(503, "插件服务未就绪")
    return service


class PluginConfigBody(BaseModel):
    """插件配置请求体（字段由插件 schema 定义）。"""

    config: dict[str, Any] = {}


@router.get("")
async def list_plugins(user=Depends(require_admin),
                       service=Depends(get_plugin_service)) -> list[dict]:
    """全部可用插件及其安装/配置状态（不含敏感值）。"""
    return service.list_states()


@router.post("/{plugin_id}/install", status_code=201)
async def install_plugin(plugin_id: str, body: PluginConfigBody,
                         user=Depends(require_admin),
                         service=Depends(get_plugin_service)) -> dict:
    """安装插件（填写配置即安装：注册工具、挂技能、播种专家）。

    Raises:
        HTTPException: 插件不存在（404）、必填配置缺失（422）。
    """
    try:
        return await service.install(plugin_id, body.config)
    except KeyError:
        raise HTTPException(404, f"插件不存在: {plugin_id}")
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.put("/{plugin_id}/config")
async def update_plugin_config(plugin_id: str, body: PluginConfigBody,
                               user=Depends(require_admin),
                               service=Depends(get_plugin_service)) -> dict:
    """更新已安装插件的配置（敏感字段留空 = 保持原值）。

    Raises:
        HTTPException: 插件不存在（404）、未安装（409）、必填缺失（422）。
    """
    try:
        return await service.update_config(plugin_id, body.config)
    except KeyError:
        raise HTTPException(404, f"插件不存在: {plugin_id}")
    except ValueError as exc:
        detail = str(exc)
        raise HTTPException(409 if "未安装" in detail else 422, detail)
```

- [ ] **Step 4: lifespan 装配 + 注册路由**

修改 `apps/web/backend/app/main.py`：

1. import 区追加（API 模块在 `app/plugins/api.py`）：

```python
from app.plugins.api import router as plugins_router
```

2. 追加插件框架 import：

```python
from app.plugins import PluginConfigStore, PluginService, plugin_roots, scan_plugins
from app.services.tool_registry import REGISTRY
```

3. lifespan 中，把 `SkillService` 构造与 `seed_builtins` 之间插入插件扫描，并把注册表交给 PluginService。改造后的 lifespan 相关片段：

```python
    app.state.project_service = ProjectService(store, settings.data_root)
    # 插件框架：扫描插件包 → 技能服务带上已安装插件的技能根 → 注册其工具
    packages = scan_plugins(plugin_roots(settings))
    plugin_config_store = PluginConfigStore(store, settings.fernet_key)
    installed_ids = await plugin_config_store.installed_ids()
    extra_roots = [
        pkg.skills_root for pid in installed_ids
        if (pkg := packages.get(pid)) is not None and pkg.skills_root is not None
    ]
    app.state.plugin_packages = packages
    app.state.plugin_config_store = plugin_config_store
    app.state.skill_service = SkillService(settings.data_root, extra_roots=extra_roots)
    app.state.skill_service.seed_builtins()  # 幂等：内置技能是列表能列出它们的前提
    app.state.weknora_service = WeKnoraService(
        settings.weknora_base_url, settings.weknora_api_key)
    app.state.plugin_service = PluginService(
        registry=REGISTRY, config_store=plugin_config_store, packages=packages,
        skill_service=app.state.skill_service,
        assistant_repo=app.state.assistant_repo,
    )
    await app.state.plugin_service.startup()
    app.state.agent_service = AgentService(
        store, settings, app.state.event_repo, app.state.skill_service,
        file_repo=app.state.file_repo)
    await seed_assistants(store)
```

4. `create_app()` 里追加路由（放在 `include_router(knowledge_router)` 之后）：

```python
    app.include_router(plugins_router)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_plugins_api.py -v
```

预期：6 passed。

- [ ] **Step 6: 全量回归**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest -q
```

预期：全部通过。

- [ ] **Step 7: 提交**

```bash
cd "E:\agent_projects\Synlora" && git add apps/web/backend/app/plugins/api.py apps/web/backend/app/main.py apps/web/backend/tests/test_plugins_api.py && git commit -m "feat: 插件管理 API 与 lifespan 装配（安装即注册工具/挂技能/播种专家）"
```

---

## Task 8: 运行期注入插件配置（ctx.extra["plugins"]）

**Files:**
- Modify: `apps/web/backend/app/services/agent_service.py`（构造签名 + `context_extra`）
- Modify: `apps/web/backend/app/main.py`（把 plugin_service 传给 AgentService）
- Test: `apps/web/backend/tests/test_chat_api.py`（既有 context_extra 断言补 1 行）

- [ ] **Step 1: 写失败测试**

在 `apps/web/backend/tests/test_chat_api.py` 里断言 `context_extra` 技能字段的那个测试（约 1047-1049 行，形如 `extra = captured[-1]["context_extra"]`）之后追加一行：

```python
    # 插件配置命名空间注入（未安装插件时为空 dict）
    assert "plugins" in extra
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_chat_api.py -q
```

预期：FAIL（`KeyError: 'plugins'`）。

- [ ] **Step 3: 注入插件上下文**

`app/services/agent_service.py`：

1. `AgentService.__init__` 增加可选参数（追加在 `file_repo` 之后）：

```python
    def __init__(self, store: Any, settings: Any, event_repo: Any,
                 skill_service: SkillService, file_repo: Any = None,
                 plugin_service: Any = None) -> None:
```

docstring 的 Args 段追加：

```
            plugin_service: 插件服务（提供已安装插件的解密配置；None = 无插件）。
```

并在方法体末尾保存：

```python
        self._plugin_service = plugin_service
```

2. `context_extra` 字典末尾（`approval_handler` 之后）追加：

```python
                    # 插件配置命名空间（已安装插件的解密配置；核心不认识任何插件字段）
                    "plugins": (self._plugin_service.context_extra()
                                if self._plugin_service is not None else {}),
```

- [ ] **Step 4: 传入 plugin_service**

`app/main.py` 的 AgentService 构造改为：

```python
    app.state.agent_service = AgentService(
        store, settings, app.state.event_repo, app.state.skill_service,
        file_repo=app.state.file_repo, plugin_service=app.state.plugin_service)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest -q
```

预期：全部通过。

- [ ] **Step 6: 提交**

```bash
cd "E:\agent_projects\Synlora" && git add apps/web/backend/app/services/agent_service.py apps/web/backend/app/main.py apps/web/backend/tests/test_chat_api.py && git commit -m "feat: 运行期按命名空间注入插件配置到 ctx.extra"
```

---

## Task 9: 前端插件管理页（schema 驱动表单）

**Files:**
- Modify: `src/types.ts`、`src/stores/admin.ts`、`src/routing/route.ts`、`src/App.tsx`、`src/components/admin/AdminLayout.tsx`、`src/components/admin/index.ts`
- Create: `src/components/admin/PluginsAdmin.tsx`

参考实现（照抄对象，不要自己发挥）：`src/components/admin/ModelsAdmin.tsx`（列表 + `Modal` 表单 + 密码输入框 + `KeyBadge` 已配置徽标 + 提交后刷新）、`src/components/admin/shared.tsx`（`Modal`/`GrayBadge`/`FormError`/`Spinner`）、`src/components/admin/form.ts`（`inputClass`/`labelClass`/`primaryButtonClass`/`secondaryButtonClass`/`errorText`）、`src/api/client.ts`（`api<T>`）。

- [ ] **Step 1: 加类型**

`src/types.ts` 追加：

```ts
/** 插件配置字段 schema（对应后端 app/plugins/loader.py 的 config_schema）。 */
export interface PluginConfigField {
  key: string;
  label: string;
  type: 'text' | 'password';
  required?: boolean;
  secret?: boolean;
  placeholder?: string;
  description?: string;
}

/** 插件状态（对应后端 app/plugins/api.py GET /plugins）。 */
export interface PluginInfo {
  id: string;
  name: string;
  version: string;
  description: string;
  config_schema: PluginConfigField[];
  installed: boolean;
  configured: boolean;
  missing: string[];
  /** 非敏感字段的当前值（敏感字段不回传） */
  config: Record<string, string>;
  /** 敏感字段是否已配置（true 时输入框留空表示保持不变） */
  secrets_set: Record<string, boolean>;
}
```

- [ ] **Step 2: 加 store 动作**

`src/stores/admin.ts` 里照 `loadProviders` 的写法追加（同一 store 或新建 `usePluginsStore`，**跟随该文件既有风格**）：

```ts
  /** 加载插件列表（含配置 schema 与安装状态）。 */
  loadPlugins: async () => {
    const plugins = await api<PluginInfo[]>('/api/v1/plugins');
    set({ plugins });
  },
```

并在 state 初始值加 `plugins: [] as PluginInfo[]`（导入 `PluginInfo` 类型）。

- [ ] **Step 3: 写插件管理页**

创建 `src/components/admin/PluginsAdmin.tsx`：结构照 `ModelsAdmin.tsx`——

- 顶部一行卡片列表（照 `SkillsAdmin` 的行卡片列表风格）：每项显示 `name` + `version` 灰徽标、`description`、状态徽标（已安装/未安装；已安装时显示 `configured ? '已配置' : '待补配置：' + missing.join(', ')`）、右侧「安装」/「配置」按钮。
- 点按钮打开 `Modal`，表单**按 `config_schema` 动态渲染**：

```tsx
{plugin.config_schema.map((field) => (
  <div key={field.key}>
    <label className={labelClass}>
      {field.label}
      {field.required ? <span className="text-[var(--sa-alias-error)]"> *</span> : null}
    </label>
    <input
      type={field.type === 'password' ? 'password' : 'text'}
      value={form[field.key] ?? ''}
      onChange={(e) => setForm((f) => ({ ...f, [field.key]: e.target.value }))}
      placeholder={
        field.type === 'password' && plugin.secrets_set[field.key]
          ? '留空保持不变'
          : field.placeholder ?? ''
      }
      autoComplete={field.type === 'password' ? 'new-password' : 'off'}
      className={inputClass}
    />
    {field.description ? (
      <p className="mt-1 text-[12px] text-[var(--sa-alias-label-secondary)]">{field.description}</p>
    ) : null}
  </div>
))}
```

- 表单初值：`installed` 时用 `plugin.config`（非敏感字段回填；敏感字段永远空），否则空对象。
- 提交：没装过用 `POST /api/v1/plugins/{id}/install`（`body: { config: form }`），已装用 `PUT /api/v1/plugins/{id}/config`；成功后 `await loadPlugins()`、`toast('success', ...)`、关闭弹窗；失败 `errorText(err)` 内联显示（照 `ModelsAdmin` 的 `FormError` 用法）。
- 无 `installed` 时区分按钮文案：「安装」/「配置」。

（完整组件请按上述三点 + ModelsAdmin 的结构写出，不要引入新依赖、不要自造样式类；只用 `--sa-*` token 与既有样式常量。）

- [ ] **Step 4: 接入路由与导航**

1. `src/routing/route.ts`：`AdminTab` 联合加 `'plugins'`；`parseAppRoute` 的正则改为 `/admin/(models|assistants|skills|plugins)`；相应类型/注释同步。
2. `src/components/admin/AdminLayout.tsx`：`TABS` 加 `{ key: 'plugins', label: '插件' }`（跟随既有 key/label 写法）。
3. `src/App.tsx`：`route.tab` 分支加 `plugins` → `<PluginsAdmin />`，并在 import 区加入（`src/components/admin/index.ts` 同步导出 `PluginsAdmin`）。

- [ ] **Step 5: 构建验证**

```bash
cd "E:\agent_projects\Synlora\apps\web\frontend" && npm run build
```

预期：`tsc -b` 无类型错误、vite 构建成功。

- [ ] **Step 6: 提交**

```bash
cd "E:\agent_projects\Synlora" && git add apps/web/frontend/src && git commit -m "feat: 插件管理页（按插件 schema 动态渲染配置表单）"
```

---

## Task 10: 文档、配置与版本

**Files:**
- Modify: `README.md`、`apps/web/backend/README.md`、`CLAUDE.md`、backlog、`app/version.py`、`frontend/package.json`

- [ ] **Step 1: backend README 新增「插件」一节**

在配置表之后新增一节，写明：插件包目录 `apps/web/backend/plugins/<id>/`（`plugin.json` + `tools.py` + `skills/`）；管理入口 = 管理后台「插件」页（按插件声明的 schema 填写服务地址/凭证，凭证 Fernet 加密落库，**不写 .env**）；新增子平台步骤 = 复制一个插件目录改 manifest（宿主与 harness 零改动）；Spec_Agent 插件的两种鉴权口径（服务端 `AUTH_ENABLED=false` 时凭证留空；生产用共享 `AUTH_SECRET` 给服务账号签长期 HMAC token 后填入）。

- [ ] **Step 2: 根 README**

「后续路线」把 AI⁴MS 接入更新为「首期：Spec_Agent 核磁三件套（插件化接入）✅ 0.5.0」，并加一段「插件机制」说明（一切皆插件：插件包目录 + manifest + 页面填配置，主框架与 harness 零改动；异步谱图任务待统一 Job 注册表）。

- [ ] **Step 3: CLAUDE.md**

关键架构约定里把沙箱那条之后补一条：

```
- 一切皆插件：子平台接入 = `apps/web/backend/plugins/<id>/`（plugin.json 声明配置 schema/工具模块/技能/专家模板）；宿主通用框架 `app/plugins/`（loader 扫描 + config_store 加密落库 + PluginService 编排 + api 管理端点）；配置经管理页填写落库（不进 settings.py/.env），运行期按命名空间注入 ctx.extra["plugins"]；技能由插件目录提供（SkillService 额外技能根），新增子平台不改主框架与 harness
```

- [ ] **Step 4: backlog 更新**

阶段二·5 拆分为：首期同步核磁三件套 ✅（0.5.0，插件化接入：插件框架 + spec_agent 插件包 + 管理页）；「统一 Job 注册表」保留为下一步（异步 5 种谱图解析建在其上）。并在「已完成（2026-09-14）」追加一条。

- [ ] **Step 5: 版本**

`app/version.py` → `0.5.0-beta.1`；`frontend/package.json` → `0.5.0-beta.1`；根 README 版本行同步。

- [ ] **Step 6: 全量验证**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest -q
cd "E:\agent_projects\Synlora\packages\synlys-harness" && conda run -n synlysagent --no-capture-output python -m pytest -q
cd "E:\agent_projects\Synlora\apps\web\frontend" && npm run build
```

预期：后端与 harness 全绿（harness 应无改动，仅回归确认）、前端构建通过。

- [ ] **Step 7: 提交**

```bash
cd "E:\agent_projects\Synlora" && git add README.md apps/web/backend/README.md CLAUDE.md docs/superpowers/plans/2026-09-12-synlysagent-06-backlog-roadmap.md apps/web/backend/app/version.py apps/web/frontend/package.json && git commit -m "docs: 插件化接入文档与版本 0.5.0-beta.1"
```

---

## 四、部署与联调（非代码任务）

1. 启动后端 → 管理后台出现「插件」页签 → Spec_Agent 显示「未安装」。
2. 点安装，填 **服务地址**（用户提供，形如 `http://10.26.15.93:<port>`）；服务端未开鉴权时凭证留空。
3. 安装后：左侧助手列表出现「谱图解析专家」；技能列表出现 `spec-nmr`（内置标记）；`spec.nmr.*` 工具对该专家可用。
4. 真机验证：选「谱图解析专家」，粘贴一组碳谱位移让它做反向预测；工具卡片显示执行中（分钟级长耗时）→ 返回候选列表。若返回「插件未配置服务地址」，去插件页补填。
5. 生产鉴权：用共享 `AUTH_SECRET` 为 Spec_Agent 侧 active 服务账号签发长期 HMAC token（`sub` 须存在于 ai4ms 用户库、`role ∈ {admin,user}`、`exp` 未过期），填入插件页「访问凭证」。

## 五、自检（Self-Review 已完成）

- **Spec 覆盖**：一切皆插件（Task 1/5/6/7/9）、配置不进主框架（Task 1 回退 + Task 3 落库 + Task 9 页面填写）、凭证加密不回传（Task 3 + Task 7）、技能不硬编码（Task 4）、工具运行期注册（Task 2 + Task 7）、运行期注入（Task 8）、文档版本（Task 10）。无遗漏。
- **占位符扫描**：无 TODO/TBD。Task 7 Step 1 末尾有一段**刻意标注为笔误、要求删除**的占位代码（含替换后的完整版本），执行时按说明处理。
- **命名一致性**：`scan_plugins` / `plugin_roots` / `load_plugin_tools` / `PluginPackage` / `PluginConfigStore.save(plugin_id, values, schema)` / `PluginService.{startup,install,update_config,state,list_states,context_extra,expert_id}` / 集合名 `plugin_configs` / 助手 id `asst-plugin-<id>` / ctx 通道 `ctx.extra["plugins"][plugin_id]` / 工具名 `spec.nmr.{forward,reverse,search}` 全文一致。
- **既有测试兼容**：`test_seed_assistants_idempotent`（`len == 2`）不受影响（v1 的 provider 门控种子已被本版删除，专家改由插件安装时播种）；`test_invalid_tool_whitelist_422` 不受影响；插件工具默认不注册（Task 2 有专门断言），故 `tests/test_plugins_api.py` 的清理夹具是必需的（共享注册表是进程级单例）。

## 六、明确不做（YAGNI）

- 不卸载插件（v1 无 uninstall；底层 `DocumentStore` 支持 `delete`，需要时再在 `PluginConfigStore`/API 上开端点）
- 不做插件市场/在线安装（插件包随仓库；`{data_dir}/plugins/` 已预留运行期安装根）
- 不做插件的 UI 贡献（插件的管理界面仍由宿主通用页面渲染 schema）
- 不做 5 种谱图异步任务（待统一 Job 注册表）
- 不做 MCP adapter（AI⁴MS 各家均无对外 MCP server）
- 不改 harness（本计划对 `packages/synlys-harness` 零改动）
