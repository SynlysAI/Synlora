# 内置内容统一到 catalog/（按类型分目录）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把内置的**专家 / 技能 / 插件**从三处异构位置（Python 常量、harness 包内资源、`backend/plugins/`）统一到一个宿主侧目录 `apps/web/backend/catalog/`，按类型分三个子目录；harness 退回"只留机制、零内容"。

**Architecture:** 内容归宿主、机制归 harness。判据：**换一个部署（另一个客户/前端/CLI 宿主）时需要改的东西 = 内容**。已核实依据：harness 内没有任何技能解析/扫描代码，它对技能的全部参与只是两个薄工具（从 `ctx.extra["skills"]` 读宿主注入的字典）；三个内置 SKILL.md 的正文句句是宿主约定（`python.run`、`file.send`、`output/`、`tmp/`、`-I`）。

**Tech Stack:** Python 3.12 / FastAPI / importlib 动态加载 / pytest；内容为纯文件

**用户已确认的决策**
1. **完全统一到 `catalog/`**，且**按类型分子目录**（`experts/` `skills/` `plugins/`）：目录位置即类型，一眼能看懂，加一个目录即扩展
2. `{data_dir}/skills` 里已播种的**同名旧副本自动清理**（仅当 SKILL.md 字节一致；内容不同 = 管理员改过，保留）

**本计划唯一一次打破"零 harness 改动"**：删 harness 的 `resources/skills/` 与 `pyproject.toml` 的两条 package-data（内容出库），需在文档注明这是有意的例外。

---

## 一、目标形态

```
packages/synlys-harness/          # 只留机制：@tool/四段管线/事件内核/LLM 流式/skill.list+skill.read/技能索引渲染
                                  #   ← 不再含任何内置内容（resources/skills 删除）

apps/web/backend/catalog/         # 内置内容（随仓库，只读）；加一个目录 = 加一个内置项
  experts/
    research-assistant/expert.json      # kind=expert（原 SEED_ASSISTANTS 的 asst-research）
    data-analyst/expert.json            # （原 asst-data）
  skills/
    data-analysis/SKILL.md              # 无需额外文件：frontmatter 即元数据
    pdf-extraction/SKILL.md
    office-doc/SKILL.md
  plugins/
    spec_agent/{plugin.json, tools.py, skills/spec-nmr/SKILL.md}   # 原样搬来；插件自带技能/专家
```

**自动扫描规则（位置即契约，放错位置会因缺字段被跳过并告警）**

| 路径 | 类型 | 判据文件 |
|---|---|---|
| `catalog/experts/<dir>/expert.json` | 专家 | 必填 `id`/`name`/`system_prompt` |
| `catalog/skills/<name>/SKILL.md` | 技能 | frontmatter 的 `name`/`description` |
| `catalog/plugins/<id>/plugin.json` | 插件 | 现有契约不变（`id`/`name`/`version`/`tools_module`） |
| `{data_dir}/catalog/{experts,skills,plugins}/` | 同上（运行期安装预留） | 同上 |

**关键约束（保 id 不变 → 无缝升级）**
- 插件 `id` 仍为 `spec_agent`（工具名 `spec.nmr.*`、`ctx.extra["plugins"]["spec_agent"]`、`plugin_configs/_id`、助手 `asst-plugin-spec_agent` 都依赖它）；**只搬目录，不改 id、不改 `plugin.json` 内容**
- 专家 `id` 仍为 `asst-research` / `asst-data`（DB 行按 `_id` 幂等播种）

## 二、File Structure

| 动作 | 路径 | 说明 |
|---|---|---|
| 新建 | `catalog/experts/{research-assistant,data-analyst}/expert.json` | 从 `SEED_ASSISTANTS` 搬 |
| 移动 | harness `resources/skills/{data-analysis,pdf-extraction,office-doc}/SKILL.md` → `catalog/skills/<同名>/SKILL.md` | 内容出 harness |
| 移动 | `apps/web/backend/plugins/spec_agent/**` → `catalog/plugins/spec_agent/**` | 原样，`plugin.json` 不改 |
| 删除 | `packages/synlys-harness/src/synlys_harness/resources/`、`apps/web/backend/plugins/` | 内容出库 |
| 修改 | `packages/synlys-harness/pyproject.toml` | 删两条 package-data + 注释 |
| 新建 | `apps/web/backend/app/catalog/loader.py` | `catalog_roots` / `scan_catalog` / `CatalogIndex` / `ExpertPackage` / `SkillPackage` / `PluginPackage` / `load_plugin_tools` |
| 删除 | `apps/web/backend/app/plugins/loader.py` | 解析逻辑并入 catalog/loader.py（单点发现） |
| 改造 | `app/catalog/items.py` | 三类条目来自 `CatalogIndex`；去 `SEED_ASSISTANTS` / `BUILTIN_SKILL_NAMES` |
| 改造 | `app/services/skill_service.py` | catalog 技能根作为只读根；`builtin` 语义改为"来自只读根"；删 `seed_builtins`/`BUILTIN_SKILL_NAMES`；旧副本迁移清理 |
| 改造 | `app/db/repos.py` → `app/catalog/seed.py` | 删 `SEED_ASSISTANTS`；播种改由 catalog 专家包驱动 |
| 改造 | `app/main.py`、`app/plugins/service.py` | 装配用 `catalog_roots`/`scan_catalog`；`PluginService` 收 `index.plugins` |
| 更新 | 测试 4 个文件 + 文档 3 个文件 | 路径与语义同步 |

命令工作目录：后端 `E:\agent_projects\Synlora\apps\web\backend`。全部使用**完整绝对 Windows 路径**。

---

## Task 1: 内容出 harness + 类型目录 + 统一扫描器

**Files:**
- Create: `catalog/experts/{research-assistant,data-analyst}/expert.json`
- Move: 3 个 SKILL.md → `catalog/skills/<同名>/`；`plugins/spec_agent/**` → `catalog/plugins/spec_agent/**`
- Delete: `packages/synlys-harness/src/synlys_harness/resources/`、`apps/web/backend/plugins/`
- Modify: `packages/synlys-harness/pyproject.toml`
- Create: `app/catalog/loader.py`；Delete: `app/plugins/loader.py`
- Modify: `app/catalog/items.py`、`app/plugins/service.py`、`app/main.py`、`app/services/skill_service.py`（仅改根）、4 个测试文件

- [ ] **Step 1: 建两个专家包**

`catalog/experts/research-assistant/expert.json`（内容从 `app/db/repos.py` 的 `SEED_ASSISTANTS[0]` 逐字搬）：

```json
{
  "id": "asst-research",
  "name": "科研助手",
  "avatar": "🧪",
  "description": "文献检索、数据分析、文件处理通用科研助手",
  "system_prompt": "你是 SynlysAgent 科研助手，帮助科研人员完成文献检索、数据分析、文件处理等任务。回答保持准确、简洁，需要时主动使用工具。",
  "tool_whitelist": ["file.read", "file.write", "file.list", "python.run", "knowledge.search", "http.request"]
}
```

`catalog/experts/data-analyst/expert.json`（同法，从 `SEED_ASSISTANTS[1]` 搬）：

```json
{
  "id": "asst-data",
  "name": "数据分析助手",
  "avatar": "📊",
  "description": "优先用 python.run 做统计分析与可视化",
  "system_prompt": "你是数据分析助手。优先使用 python.run 工具对用户上传的数据做统计分析与可视化，结果图表保存到 output/ 目录并在回复中说明结论。",
  "tool_whitelist": ["python.run", "file.read", "file.write", "file.list"]
}
```

（技能与插件**不需要**额外 manifest：技能靠 `SKILL.md` frontmatter，插件沿用 `plugin.json`。）

- [ ] **Step 2: 搬文件**

```bash
cd "E:/agent_projects/Synlora"
mv packages/synlys-harness/src/synlys_harness/resources/skills/data-analysis/SKILL.md apps/web/backend/catalog/skills/data-analysis/SKILL.md
mv packages/synlys-harness/src/synlys_harness/resources/skills/pdf-extraction/SKILL.md apps/web/backend/catalog/skills/pdf-extraction/SKILL.md
mv packages/synlys-harness/src/synlys_harness/resources/skills/office-doc/SKILL.md apps/web/backend/catalog/skills/office-doc/SKILL.md
rm -rf packages/synlys-harness/src/synlys_harness/resources
mv apps/web/backend/plugins/spec_agent apps/web/backend/catalog/plugins/spec_agent
rm -rf apps/web/backend/plugins
```

- [ ] **Step 3: 摘掉 harness 打包声明**

`packages/synlys-harness/pyproject.toml`：删掉末尾这一整块：

```toml
# 内置技能种子（resources 不是包，无 __init__.py，必须显式声明随包安装，
# 否则正式安装后 SkillService.seed_builtins 找不到 src 树）。
[tool.setuptools.package-data]
synlys_harness = [
    "resources/skills/**/*",
    "resources/skills/*/*",
]
```

- [ ] **Step 4: 统一扫描器 `app/catalog/loader.py`**

把 `app/plugins/loader.py` 的内容搬来并扩展（**删掉原文件**）。要点：

```python
"""内置内容（catalog）扫描：专家 / 技能 / 插件。

目录位置即类型（用户视角一眼可辨，加一个目录即扩展）：
    <root>/experts/<dir>/expert.json    → 专家
    <root>/skills/<name>/SKILL.md       → 技能（frontmatter 即元数据，无额外 manifest）
    <root>/plugins/<id>/plugin.json     → 插件（沿用既有插件契约）
两个根：随仓库的 `apps/web/backend/catalog/` + `{data_dir}/catalog/`（运行期安装预留）。
非法/缺字段/放错位置的包只告警跳过，不阻断启动。
"""

EXPERTS_DIR = "experts"
SKILLS_DIR = "skills"
PLUGINS_DIR = "plugins"


@dataclass(frozen=True)
class ExpertPackage:
    """内置专家包（catalog/experts/<dir>/expert.json）。"""

    id: str
    name: str
    avatar: str
    description: str
    system_prompt: str
    tool_whitelist: list[str]
    directory: Path


@dataclass(frozen=True)
class SkillPackage:
    """内置技能包（catalog/skills/<name>/SKILL.md）。

    description 由 CatalogService 从 SKILL.md frontmatter 解析（此处不重复解析）。
    """

    name: str
    directory: Path


@dataclass(frozen=True)
class CatalogIndex:
    """一次扫描的结果（三类分开，调用方各取所需）。"""

    experts: dict[str, ExpertPackage]
    skills: dict[str, SkillPackage]
    plugins: dict[str, "PluginPackage"]


def catalog_roots(settings: "Settings") -> list[Path]:
    """内置内容搜索根。

    Args:
        settings: 应用配置（取数据目录）。

    Returns:
        根目录列表：随仓库的 `apps/web/backend/catalog/` + `{data_dir}/catalog/`。
    """
    return [
        Path(__file__).resolve().parents[2] / "catalog",
        settings.data_root / "catalog",
    ]


def scan_catalog(roots: list[Path]) -> CatalogIndex:
    """扫描全部内置内容根。

    Args:
        roots: 根目录列表（不存在的根跳过）。

    Returns:
        CatalogIndex；三类条目各自的非法包只告警跳过，同名后者覆盖前者。
    """
```

实现细节：
- `PluginPackage`、`load_plugin_tools`、以及插件 manifest 解析逻辑**从原文件原样搬来**（`MANIFEST_NAME="plugin.json"`、`REQUIRED_FIELDS=("id","name","version","tools_module")`、config_schema 形状校验、`skills_root` property、`plugins` 字段等一律不变），只把根目录从 `<root>` 改为 `<root>/plugins`
- `skills` 扫描：`<root>/skills/<dir>/SKILL.md` 存在即收录 `SkillPackage(name=dir.name, directory=dir)`
- `experts` 扫描：读 `expert.json`，缺 `id`/`name`/`system_prompt` 告警跳过；`avatar`/`description`/`tool_whitelist` 缺省空

- [ ] **Step 5: 技能服务只改根路径（本步不删播种）**

`app/services/skill_service.py`：`RESOURCE_SKILLS` 的计算改为指向 **`catalog/skills`**：

```python
# 内置技能源目录（随仓库内容：apps/web/backend/catalog/skills）
CATALOG_SKILLS_ROOT = Path(__file__).resolve().parents[2] / "catalog" / "skills"
```

`seed_builtins()` 暂时保留"拷贝到 `{data_root}/skills`"的旧行为（本步不改语义，保证测试仍绿）；Task 4 再切换为只读根 + 迁移清理。

- [ ] **Step 6: 更新引用与测试**

- `app/plugins/service.py`：`packages: dict[str, PluginPackage]` 参数语义不变（现在来自 `index.plugins`），import 改为 `from app.catalog.loader import PluginPackage, load_plugin_tools`
- `app/catalog/items.py`：import 改为新模块；构造参数从"packages"改为 `index: CatalogIndex`（内部实现 Task 3 再改，本步先机械替换 import/签名并让它取 `index.plugins`）
- `app/main.py`：`index = scan_catalog(catalog_roots(settings))`；`PluginService(packages=index.plugins, ...)`
- 测试：
  - `tests/test_plugin_loader.py` → 改名 `tests/test_catalog_loader.py`，覆盖三类扫描（专家/技能/插件各一条 + 非法包跳过 + 空根返回空）；插件相关断言沿用（manifest 仍是 `plugin.json`）
  - `tests/test_spec_agent_plugin.py`：`REPO_PLUGINS = Path(__file__).resolve().parents[1] / "catalog" / "plugins"`
  - `tests/test_catalog_items.py` / `tests/test_catalog_service.py`：`plugin_roots/scan_plugins` → `catalog_roots/scan_catalog`（取 `.plugins`）
  - `tests/test_repos.py` 的种子用例：本步仍用旧 `SEED_ASSISTANTS`（Task 2 切换）

- [ ] **Step 7: 全量验证**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest -q
cd "E:\agent_projects\Synlora\packages\synlys-harness" && conda run -n synlysagent --no-capture-output python -m pytest -q
```

预期：后端全绿（技能仍走拷贝，行为不变）；harness 145 passed（harness 测试只通过 `ctx.extra["skills"]` 传字典，不读资源目录，删除无影响）。

- [ ] **Step 8: 提交**

```bash
cd "E:\agent_projects\Synlora" && git add -A && git commit -m "refactor: 内置内容迁入 catalog/（按类型分目录），统一扫描器"
```

---

## Task 2: 专家种子改由 catalog 驱动

**Files:**
- Create: `apps/web/backend/app/catalog/seed.py`
- Modify: `apps/web/backend/app/db/repos.py`（删 `SEED_ASSISTANTS` 与 `seed_assistants`）、`app/main.py`
- Test: `tests/test_repos.py`（种子用例迁到新测试文件 `tests/test_catalog_seed.py`）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_catalog_seed.py`：

```python
"""专家种子（来自 catalog/experts 包）测试。"""
from __future__ import annotations

from pathlib import Path

from app.catalog.loader import catalog_roots, scan_catalog
from app.catalog.seed import seed_experts
from app.core.settings import Settings

REPO_CATALOG = Path(__file__).resolve().parents[1] / "catalog"


def _experts():
    """仓库 catalog 里的专家包。"""
    return scan_catalog([REPO_CATALOG]).experts


async def test_experts_come_from_catalog(store):
    """专家包来自 catalog/experts（不再是代码常量）。"""
    experts = _experts()
    assert set(experts) == {"asst-research", "asst-data"}
    assert experts["asst-research"].name == "科研助手"
    assert "python.run" in experts["asst-research"].tool_whitelist


async def test_seed_experts_idempotent_and_self_healing(store):
    """按 _id 幂等播种；缺失项补种，已存在项不覆盖。"""
    experts = _experts()
    await seed_experts(store, experts)
    await seed_experts(store, experts)
    assert len(await store.list("assistants")) == 2

    await store.update("assistants", "asst-research", {"name": "改过的名字"})
    await seed_experts(store, experts)
    assert (await store.get("assistants", "asst-research"))["name"] == "改过的名字"

    await store.delete("assistants", "asst-data")
    await seed_experts(store, experts)
    assert await store.get("assistants", "asst-data") is not None


async def test_seeded_experts_are_builtin_flagged(store):
    """播种的专家标记 builtin=True（不可删）。"""
    await seed_experts(store, _experts())
    assert (await store.get("assistants", "asst-research"))["builtin"] is True
```

- [ ] **Step 2: 实现**

创建 `app/catalog/seed.py`：

```python
"""把 catalog 专家包播种成助手文档（按 _id 幂等，缺失补种）。

与插件播种（PluginService._seed_expert）的区别：这里播的是"随仓库内置的独立专家"，
插件播种的专家（asst-plugin-*）跟随其插件，不在此列。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.catalog.loader import ExpertPackage


async def seed_experts(store: Any, experts: dict[str, "ExpertPackage"]) -> None:
    """逐条按 _id 幂等插入内置专家。

    Args:
        store: DocumentStore 实例。
        experts: catalog 专家包（{id: ExpertPackage}）。
    """
```

实现照搬原 `seed_assistants` 的幂等与自愈语义（已存在的跳过；`repo.create` 字段：`_id`/`name`/`avatar`/`description`/`system_prompt`/`tool_whitelist`/`builtin=True`），只是数据源换成专家包。

`app/db/repos.py`：删除 `SEED_ASSISTANTS` 常量与 `seed_assistants` 函数（若 `AssistantRepo` 等仍被引用则保留）。
`app/main.py`：lifespan 中 `await seed_experts(store, index.experts)`；import 调整。
`tests/test_repos.py`：删掉两个 `seed_assistants` 用例（已被新文件覆盖）。

- [ ] **Step 3: 跑测试 + 提交**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_catalog_seed.py tests/test_repos.py -q && conda run -n synlysagent --no-capture-output python -m pytest -q
cd "E:\agent_projects\Synlora" && git add -A && git commit -m "refactor: 内置专家种子改由 catalog/experts 驱动"
```

---

## Task 3: CatalogService 三类条目一律来自 CatalogIndex

**Files:**
- Modify: `apps/web/backend/app/catalog/items.py`
- Test: `tests/test_catalog_items.py`

- [ ] **Step 1: 改测试**

`tests/test_catalog_items.py` 的 `_service()` 改为传 `scan_catalog([REPO_CATALOG])` 的结果；断言：

```python
def test_items_come_from_catalog(tmp_path):
    """三类条目均来自 catalog/（专家不再来自代码常量、技能不再来自内置名单）。"""
    svc = _service(...)
    assert {i.id for i in svc.list_items("expert")} == {"asst-research", "asst-data"}
    assert {i.id for i in svc.list_items("skill")} == {
        "data-analysis", "pdf-extraction", "office-doc"}
    assert "spec_agent" in {i.id for i in svc.list_items("plugin")}


def test_skill_description_comes_from_skill_md(tmp_path):
    """技能条目的 description 取自 SKILL.md frontmatter（单一来源）。"""
    svc = _service(...)
    office = next(i for i in svc.list_items("skill") if i.id == "office-doc")
    assert "生成 Word" in office.description
```

- [ ] **Step 2: 实现（`app/catalog/items.py`）**

- **删** `from app.db.repos import SEED_ASSISTANTS`（Task 2 已删该常量）与 `from app.services.skill_service import BUILTIN_SKILL_NAMES`
- 构造签名改为 `CatalogService(index: CatalogIndex)`（去掉 `settings`/`skill_service`/`packages` 三个参数——技能描述改从 `SKILL.md` 解析，不再依赖技能服务）

```python
    def _experts(self) -> list[CatalogItem]:
        """内置专家条目（catalog/experts 包）。

        Returns:
            条目列表（按 id 排序）。
        """
        return sorted(
            (CatalogItem(kind="expert", id=p.id, name=p.name, description=p.description)
             for p in self._index.experts.values()),
            key=lambda i: i.id,
        )

    def _skills(self) -> list[CatalogItem]:
        """内置技能条目（catalog/skills 包，描述取 SKILL.md frontmatter）。

        Returns:
            条目列表（按 id 排序）。
        """
        out: list[CatalogItem] = []
        for pkg in self._index.skills.values():
            md = pkg.directory / "SKILL.md"
            try:
                meta = parse_skill_md(md.read_text(encoding="utf-8"))
                desc = str(meta.get("description") or "")
            except (OSError, ValueError, yaml.YAMLError):
                logger.warning("catalog 技能 %s 的 SKILL.md 解析失败，描述留空", pkg.name)
                desc = ""
            out.append(CatalogItem(kind="skill", id=pkg.name, name=pkg.name, description=desc))
        return sorted(out, key=lambda i: i.id)

    def _plugins(self) -> list[CatalogItem]:
        """内置插件条目（catalog/plugins 包）。"""
        return sorted(
            (CatalogItem(kind="plugin", id=p.id, name=p.name, description=p.description)
             for p in self._index.plugins.values()),
            key=lambda i: i.id,
        )
```

- 删 `EXPERT_ID_PREFIX` 与相关过滤（插件播种的专家不在 catalog 里，天然排除）
- `packages` property 改为 `plugins`（返回 `self._index.plugins`；调用方 `CapabilityService.visible_skill_names`/`market_items` 用的是"插件包"语义，签名不变、只是取值来源变了——同步更新 `app/catalog/service.py` 里的 `self.catalog.packages` → `self.catalog.plugins`）
- import 顶部补 `parse_skill_md`、`yaml`、`logging`/`logger`
- 模块 docstring 改为说明三条扫描规则

- [ ] **Step 3: 跑测试 + 提交**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest tests/test_catalog_items.py tests/test_catalog_service.py tests/test_capability_enforcement.py -q && conda run -n synlysagent --no-capture-output python -m pytest -q
cd "E:\agent_projects\Synlora" && git add -A && git commit -m "refactor: 能力目录条目统一由 catalog 包驱动"
```

---

## Task 4: 技能改只读根 + 旧副本迁移清理 + 删两处硬编码

**Files:**
- Modify: `apps/web/backend/app/services/skill_service.py`、`app/main.py`
- Test: `tests/test_skill_service.py`、`tests/test_catalog_service.py`、`tests/test_capability_enforcement.py`

**语义切换（本任务核心）**
- `catalog/skills` 作为**只读技能根**直接提供服务（不再 `seed_builtins()` 拷进 `{data_dir}/skills`）——与插件技能根统一
- `{data_dir}/skills` 退回**公共层**（管理员自建/导入，始终可见）
- `builtin` 语义改为「**来自只读根**（catalog 根 / 插件根）」，不再靠名字名单
- **迁移**：启动时清理 `{data_dir}/skills/` 里与 catalog 同名且 `SKILL.md` **字节一致**的旧副本；不一致（管理员改过）保留

- [ ] **Step 1: 改 `skill_service.py`**

- 删 `BUILTIN_SKILL_NAMES`；`_scan_root` 里 `skill["builtin"] = builtin or skill["name"] in BUILTIN_SKILL_NAMES` → `skill["builtin"] = builtin`（只读根传 True）
- 删 `seed_builtins()`；新增迁移方法：

```python
    def migrate_legacy_builtin_copies(self, catalog_skills_root: Path) -> list[str]:
        """清理公共技能目录里与 catalog 一致的旧内置副本（播种遗留）。

        只删「同名且 SKILL.md 字节完全一致」的目录——内容不同说明管理员改过，保留。

        Args:
            catalog_skills_root: catalog 技能根（其下每个子目录是一个内置技能）。

        Returns:
            被清理的技能名列表。
        """
```

- `delete_skill`：删掉 `if name in BUILTIN_SKILL_NAMES: raise ValueError(...)` 分支（只读根技能本就不在可写目录，删除自然返回 False）；相应地把"内置技能不可删除"的 docstring 一并清理
- `write_skill`：返回值 `builtin` 恒为 `False`

- [ ] **Step 2: 改 `main.py`**

```python
    catalog_index = scan_catalog(catalog_roots(settings))
    catalog_skills_root = catalog_roots(settings)[0] / "skills"
    app.state.skill_service = SkillService(settings.data_root, extra_roots=[catalog_skills_root])
    removed = app.state.skill_service.migrate_legacy_builtin_copies(catalog_skills_root)
    if removed:
        logger.info("已清理迁移前的内置技能旧副本: %s", removed)
```

- [ ] **Step 3: 改测试**

- `tests/test_skill_service.py`：删 `test_seed_builtins_is_idempotent`，补两条：

```python
def test_migrate_removes_identical_legacy_copies(tmp_path):
    """迁移清理：与 catalog 字节一致的旧副本被删；内容不同（管理员改过）保留。"""
    catalog_root = tmp_path / "catalog" / "skills"
    (catalog_root / "office-doc").mkdir(parents=True)
    (catalog_root / "office-doc" / "SKILL.md").write_text(
        "---\nname: office-doc\ndescription: 生成办公文档\n---\n正文\n", encoding="utf-8")
    (catalog_root / "data-analysis").mkdir(parents=True)
    (catalog_root / "data-analysis" / "SKILL.md").write_text(
        "---\nname: data-analysis\ndescription: 官方\n---\n官方正文\n", encoding="utf-8")

    svc = SkillService(tmp_path / "data")
    (svc.skills_dir / "office-doc").mkdir(parents=True)
    (svc.skills_dir / "office-doc" / "SKILL.md").write_text(
        (catalog_root / "office-doc" / "SKILL.md").read_text(encoding="utf-8"), encoding="utf-8")
    (svc.skills_dir / "data-analysis").mkdir(parents=True)
    (svc.skills_dir / "data-analysis" / "SKILL.md").write_text(
        "---\nname: data-analysis\ndescription: 我改过的\n---\n我的正文\n", encoding="utf-8")

    assert svc.migrate_legacy_builtin_copies(catalog_root) == ["office-doc"]
    assert not (svc.skills_dir / "office-doc").exists()
    assert (svc.skills_dir / "data-analysis").exists()


def test_catalog_root_skills_are_readonly_and_builtin(tmp_path):
    """catalog 根作为只读技能根：可列可读、builtin=True、删不掉。"""
    catalog_root = tmp_path / "catalog" / "skills"
    (catalog_root / "office-doc").mkdir(parents=True)
    (catalog_root / "office-doc" / "SKILL.md").write_text(
        "---\nname: office-doc\ndescription: 生成办公文档\n---\n正文\n", encoding="utf-8")

    svc = SkillService(tmp_path / "data", extra_roots=[catalog_root])
    skills = {s["name"]: s for s in svc.list_skills()}
    assert skills["office-doc"]["builtin"] is True
    assert svc.read_body("office-doc") == "正文"
    assert svc.delete_skill("office-doc") is False
    assert (catalog_root / "office-doc" / "SKILL.md").exists()
```

- `tests/test_catalog_service.py` / `tests/test_capability_enforcement.py`：`SkillService(tmp)` + `seed_builtins()` 的构造改为 `SkillService(tmp, extra_roots=[REPO_CATALOG / "skills"])`；`caps` 夹具若用 `CatalogService(settings, skill_service, packages)` 需按 Task 3 的新签名改为 `CatalogService(scan_catalog([REPO_CATALOG]))`

- [ ] **Step 4: 全量验证（关键回归）**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest -q
```

**重点确认** `tests/test_capability_enforcement.py`：普通用户能看到三个内置技能 + `spec-nmr`、被设 hidden 的内置技能看不到、公共层管理员自建技能始终可见——这些断言的"内置技能"现在来自 `catalog/skills` 只读根。

- [ ] **Step 5: 真机迁移验证**（临时脚本，跑完删）

用既有 `apps/web/data` 数据目录起一次 lifespan，确认：
- `apps/web/data/skills/{data-analysis,pdf-extraction,office-doc}` 三个旧副本被清掉
- 技能列表里这三个技能仍在（来自 catalog 根，`builtin=True`）
- 公共目录里其它（管理员自建的）技能不受影响

- [ ] **Step 6: 提交**

```bash
cd "E:\agent_projects\Synlora" && git add -A && git commit -m "refactor: 技能改由 catalog 只读根提供并清理旧副本"
```

---

## Task 5: 文档与版本

**Files:**
- Modify: `README.md`、`apps/web/backend/README.md`、`CLAUDE.md`、backlog、`app/version.py`、`frontend/package.json`

- [ ] **Step 1: 路径与语义同步**

`grep -rn "backend/plugins\|plugins/<id>\|resources/skills\|seed_builtins\|BUILTIN_SKILL_NAMES" --include=*.md .` 逐条改为：
- `apps/web/backend/plugins/<id>/` → **`apps/web/backend/catalog/plugins/<id>/`**
- 新增说明：**内置内容按类型分目录**（`catalog/{experts,skills,plugins}/`，位置即类型，加目录即扩展；`{data_dir}/catalog/` 为运行期安装预留）
- 技能相关：删"由 harness 随包发布、启动播种到数据目录"的说法，改为**内置技能在 `catalog/skills/`，作为只读技能根直接提供**；`{data_dir}/skills` = 公共层（管理员自建，始终可见）
- harness 边界：**内容归宿主、机制归 harness**；harness 内不含任何内置内容（本次删除 `resources/` 与 package-data，是有意的例外）
- backend README 补一小节「内置内容布局」，给出三种包的最小示例（`experts/<dir>/expert.json`、`skills/<name>/SKILL.md`、`plugins/<id>/plugin.json`）

- [ ] **Step 2: backlog**

新增「### 内置内容统一到 `catalog/`（按类型分目录）✅ 2026-09-15」，勾选：内容出 harness、类型目录 + 统一扫描器 `scan_catalog`、CatalogService 去两处硬编码、技能改只读根 + 旧副本迁移清理；注明这是**唯一一次**有意改动 harness。

- [ ] **Step 3: 版本（三处同步）** `0.7.0-beta.1`

`app/version.py`、`frontend/package.json`、根 `README.md` 版本行；一并提交本轮计划 `docs/superpowers/plans/2026-09-15-synlysagent-09-catalog-content-unification.md`。

- [ ] **Step 4: 全量验证**

```bash
cd "E:\agent_projects\Synlora\apps\web\backend" && conda run -n synlysagent --no-capture-output python -m pytest -q
cd "E:\agent_projects\Synlora\packages\synlys-harness" && conda run -n synlysagent --no-capture-output python -m pytest -q
cd "E:\agent_projects\Synlora\apps\web\frontend" && npm run build
cd "E:\agent_projects\Synlora" && git status --short
```

- [ ] **Step 5: 提交**

```bash
cd "E:\agent_projects\Synlora" && git add README.md CLAUDE.md apps/web/backend/README.md apps/web/backend/app/version.py apps/web/frontend/package.json docs/superpowers/plans/ && git commit -m "docs: 内置内容布局文档与版本 0.7.0-beta.1"
```

---

## 三、自检（Self-Review 已完成）

- **需求覆盖**：类型分目录（T1）、加目录即扩展（T1 扫描规则 + 文档示例）、内容出 harness（T1）、两处硬编码消失（T2 专家常量 / T3+T4 技能名单）、旧副本自动清理（T4）、文档版本（T5）。用户两条决策均已编码。
- **兼容性**：`spec_agent` 的 id、`asst-research`/`asst-data` 的 `_id`、`plugin.json` 契约全部不变 → DB 记录、工具名、配置命名空间、插件测试均不受影响；每个 Task 结束都能独立跑绿。
- **命名一致性**：`catalog_roots` / `scan_catalog` / `CatalogIndex` / `ExpertPackage` / `SkillPackage` / `PluginPackage` / `load_plugin_tools` / `seed_experts` / `migrate_legacy_builtin_copies` 全文一致。
- **风险点**：T4 切技能来源时 `test_capability_enforcement.py` 必须复跑确认；`seed_builtins` 删除后 `{data_dir}/skills` 不再自动填充，迁移必须在 lifespan 同步执行，否则首启会"少三个技能"。`CatalogService` 去掉 `settings`/`skill_service` 参数会牵动多处构造点（items/service/api/tests），T3 一并改完。

## 四、明确不做

- 不建 `packages/synlys-content/`（真有第二个宿主再说）
- 不改插件目录名/`plugin.json` 文件名（避免无谓 churn）
- 不做用户自建/导入（上轮已明确）
- 不迁移历史 plan 文档里的旧路径（历史记录）
