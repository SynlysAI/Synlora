# SynlysAgent Plan 5：侧栏层级 / 专家可选 / 输入框卡片

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 左侧栏改成 jiuwen 式「工作区 / 会话」两级结构并去掉助手选择；专家改为**可选**（不选即走平台默认提示词）；输入框按 jiuwen 空态形态重做——外层灰卡同时包住输入区与「选择工作区」行。

**Architecture:** 4 个任务。**D1 后端**让 `assistant_id` 可空（建会话、切专家、agent 装配三处）；**D2–D4 前端**分别做专家可选 UI、侧栏层级、输入框卡片。侧栏分组**纯前端**完成——`GET /api/v1/sessions` 已返回每条会话的 `project_id`，不需要新接口。

**Tech Stack:** FastAPI + 双后端 DocumentStore、React 19 + TS + Tailwind 4 + Zustand。

**参考源（照抄对象）:**
- 侧栏层级：`E:\agent_projects\jiuwenswarm\jiuwenswarm\channels\web\frontend\src\multi-session\sidebar\ConversationSidebar.tsx`（`__group` / `__section-heading` / `__label` / `__section-action` / `__group-list` / `__empty`；项目行展开用 `ProjectEntityRow`）
- 侧栏样式：同目录 `ConversationSidebar.css`；时间格式化 `sidebarModel.ts` 的 `formatRelativeTime`
- 输入框卡片：`...\components\ChatPanel\InputArea.tsx:2703-2720`（`chat-input-frame` > `chat-input-container` > `chat-input-body` + 平级的 `chat-work-context-wrapper`）与 `ChatPanel.css:125-170`（`--work-home` 外灰卡变体）、`2223-2256`（项目行）
- 未选专家的行为：`common\schema\chat_send.py:45`（`""` 卸载专家）、`InputArea.tsx:3450`（未选则不渲染 chip）

---

## 设计决策（已与你确认）

| # | 决策 |
|---|---|
| D1 | **不选专家 = 无 persona，只走平台默认提示词**（`prompts.py` 的 SOUL/AGENT/工作区/技能索引）。工具 = **全部内置工具**。与 jiuwen 的 `""` 卸载专家一致 |
| D2 | 侧栏名词用**「工作区」+「会话」**（jiuwen 是「项目」+「任务」）：抄 IA、不抄词——API/store/文案全栈都叫 session，只在 UI 改名会造出两套词汇 |
| D3 | **「会话」组 = 默认工作区下的会话**；「工作区」组列其余工作区（默认工作区不重复出现），每个工作区可展开看自己的会话，空则「暂无会话」。完全照 jiuwen 的 `regularProjects` / `conversationSessions` 划分 |
| D4 | **新建会话进默认工作区**（jiuwen 的「新建任务」就是清空 `selectedProject`）。输入框下方选择器**未选时显示「请选择工作区」**，实际按默认工作区处理 |
| D5 | 输入框卡片**只在空态**是「外灰卡 + 内白卡 + 底部工作区行」（jiuwen 的 `--work-home` 只在 `activeSessionId === NEW_CONVERSATION_ID` 时启用）；有消息后退回单层白卡、无工作区行 |
| D6 | **定时任务不搬**（我们没有 cron） |
| D7 | 侧栏保留会话搜索框（jiuwen 没有，但我们的搜索框已有且有用，不删） |

---

## 文件结构

```
apps/web/backend/app/
├── api/sessions_api.py          # 改：assistant_id 可空 + PATCH 支持清空
└── services/agent_service.py    # 改：assistant 可空 → persona 空 + 全部工具

apps/web/frontend/src/
├── components/sidebar/
│   ├── Sidebar.tsx              # 改：去掉 AssistantPicker；改为「工作区」组 + 「会话」组
│   ├── WorkspaceGroup.tsx       # 新建：工作区列表（可展开、hover + 新建）
│   └── SessionList.tsx          # 改：支持「指定一组会话 + 缩进」两种用法
├── components/chat/
│   ├── Composer.tsx             # 改：空态外灰卡 + 内白卡 + 底部工作区行
│   ├── WorkspacePicker.tsx      # 新建（从 ProjectPicker 改名/重做）：空态底部那行
│   ├── ExpertPicker.tsx         # 改：加「不使用专家」项
│   └── ProjectPicker.tsx        # 删除（被 WorkspacePicker 取代）
├── components/sidebar/index.ts  # 改：导出同步
└── stores/projects.ts           # 改：currentId 允许为 null（未选 = 用默认工作区）
```

---

## Task D1: 后端——专家可选

**Files:** Modify `apps/web/backend/app/api/sessions_api.py`、`apps/web/backend/app/services/agent_service.py`；Test `apps/web/backend/tests/test_chat_api.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
async def test_create_session_without_assistant(client, user_headers):
    """不选专家也能建会话（jiuwen 的 '' 卸载专家语义）。"""
    r = await client.post("/api/v1/sessions", json={}, headers=user_headers)
    assert r.status_code == 201
    assert not r.json().get("assistant_id")


async def test_patch_can_clear_assistant(client, user_headers):
    """PATCH 显式传 assistant_id="" 表示卸载专家；不传该字段则不动。"""
    sid = (await client.post("/api/v1/sessions",
                             json={"assistant_id": "asst-research"},
                             headers=user_headers)).json()["_id"]
    # 不传该字段 → 保持
    r1 = await client.patch(f"/api/v1/sessions/{sid}", json={"title": "x"}, headers=user_headers)
    assert r1.json()["assistant_id"] == "asst-research"
    # 显式传空 → 清空
    r2 = await client.patch(f"/api/v1/sessions/{sid}", json={"assistant_id": ""},
                            headers=user_headers)
    assert not r2.json().get("assistant_id")
    # 传非法 id → 404 且不改
    r3 = await client.patch(f"/api/v1/sessions/{sid}", json={"assistant_id": "nope"},
                            headers=user_headers)
    assert r3.status_code == 404


async def test_no_assistant_uses_platform_prompt_only(client, user_headers, ...):
    """无专家时：system prompt 有平台默认段、无 persona；工具表是全部内置工具。"""
    sid = (await client.post("/api/v1/sessions", json={}, headers=user_headers)).json()["_id"]
    # 发一条消息，捕获 agent 实参（用本文件既有的 _capture_run_args）
    ...
    assert "核心原则" in captured_prompt          # 平台段在
    assert "科研助手" not in captured_prompt       # 没有专家 persona
    # 工具表含全部内置工具
    for name in ("file.read", "python.run", "skill.list", "skill.read"):
        assert name in captured_tool_names
```

> 后两条的「捕获 agent 实参」请复用 `tests/test_chat_api.py` 里已有的 `_capture_run_args`（Task C5/B5 建的），不要另造。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/web/backend && conda run -n synlysagent --no-capture-output python -m pytest tests/test_chat_api.py -v`

- [ ] **Step 3: 实现**

**`sessions_api.py`**：

```python
class SessionCreateBody(BaseModel):
    """新建会话请求体（assistant_id 可缺省 = 不使用专家，走平台默认提示词）。"""

    assistant_id: str | None = None
    title: str = ""
    model_provider_id: str | None = None
    project_id: str | None = None


class SessionUpdateBody(BaseModel):
    """更新会话请求体（改名/归档/切换模型/切换专家）。

    model_provider_id 与 assistant_id 都用 model_fields_set 区分「未提供」与
    「显式传 null/空」：前者不动原值，后者表示清除（恢复助手默认 / 卸载专家）。
    """

    title: str | None = None
    archived: bool | None = None
    model_provider_id: str | None = None
    assistant_id: str | None = None
```

`create_session`：把 `if await repos.assistant.get(body.assistant_id) is None: raise 404` 改成「非空才校验」。

`update_session`：`assistant_id` 改成与 `model_provider_id` 同样的 `model_fields_set` 模式：

```python
    if "assistant_id" in body.model_fields_set:
        aid = body.assistant_id
        if aid:
            if await repos.assistant.get(aid) is None:
                raise HTTPException(404, "助手不存在")
            fields["assistant_id"] = aid
        else:
            fields["assistant_id"] = None   # 显式清空 = 卸载专家
```

**`agent_service.py`**：`chat(...)` 的 `assistant` 形参允许为 `None`/空 dict：

```python
    persona = str((assistant or {}).get("system_prompt") or "").strip()
    whitelist = list((assistant or {}).get("tool_whitelist") or [])
    # 无专家（或专家没限定工具）时放开全部内置工具
    tool_names = list(dict.fromkeys([*whitelist, *SKILL_TOOLS])) if whitelist else list(
        registry.names)
```

> 注意 `SKILL_TOOLS` 是既有的模块常量（`("skill.list", "skill.read")`）；`registry` 取 `self._registry`（以实际属性名为准）。**无专家时给全部内置工具**是 D1 决策。

调用方 `sessions_api.send_message` 里原本从会话取 assistant 的地方，改成「取不到就传 None」。

- [x] **Step 4: 跑测试确认通过 + 全量**

Run: `cd apps/web/backend && conda run -n synlysagent --no-capture-output python -m pytest -q`
Expected: 全绿（基线 183 passed / 36 skipped + 新增 3）

- [x] **Step 5: Commit** — `feat(web): 专家可选（不选走平台默认提示词）`

> **本任务已完成**：`acc05cc`。要点与订正：
> - **注册表是模块级 `_REGISTRY`**（不是 `self._registry`）。无专家时 `tool_names = list(_REGISTRY.names)` = 全部 8 个内置工具。
> - **`send_message` 里所有取 assistant 字段的地方都要用 `(assistant or {})`**——只把 None 传给 `chat` 不够，取模型/归属时 `.get` 会直接崩。
> - **⚠️ 前端必须处理（D2/D4 的关键输入）**：**没有专家就没有可回落的模型服务**，发消息会按既有契约 422「未指定模型服务」。所以模型选择器在「无专家」时**不能停在「跟随助手」模式**，必须显示/强制一个显式模型（建议自动选第一个 enabled 模型）。D1 的第三条测试正是靠 admin 建了 provider 才能跑通。
>
> 后端测试 183 → 186 passed。

---

## Task D2: 前端——专家 chip 可空与「不使用专家」

**Files:** Modify `components/chat/ExpertPicker.tsx`、`components/chat/Composer.tsx`、`components/chat/ChatPanel.tsx`

- [ ] **Step 1:** `ChatPanel` 里 `assistant` 现在可能取不到（`session.assistant_id` 为空）——把 `assistantName` / `assistantAvatar` 的兜底改成平台名（如 `'SynlysAgent'`），不要再用「取第一个助手」兜底。

- [ ] **Step 2:** `Composer` 的专家 chip：**只有选中专家时才渲染**（jiuwen `InputArea.tsx:3450` 的 `{selectedAgentId && ...}`）。未选时不显示 chip，「+ → 专家」仍可进入选择。

- [ ] **Step 3:** `ExpertPicker` 增加一项「不使用专家」（列表最上或最下，带分隔线）：点击 → 有当前会话则 `PATCH {assistant_id: ""}`，无会话则 `assistants.select(null)`（store 需允许 `selectedId` 为 null 且不再回退第一个）。

- [ ] **Step 4:** `stores/assistants.ts` 的 `load()` 不再在无显式选择时回退第一个（`selectedId` 保持 null）；`pickSelectedAssistant` 相应调整。**`Sidebar.tsx` 里的 `AssistantPicker` 与 `handleNew` 也要跟着改**（D3 会做，这里先保证不编译错）。

- [ ] **Step 5: 验证**：`npm run build && npm run lint`，无新增警告

- [ ] **Step 6: Commit** — `feat(frontend): 专家可选与不使用专家`

---

## Task D3: 前端——侧栏改为「工作区 / 会话」两级

**Files:** Create `components/sidebar/WorkspaceGroup.tsx`；Modify `components/sidebar/Sidebar.tsx`、`components/sidebar/SessionList.tsx`、`components/sidebar/index.ts`

**照抄**：`ConversationSidebar.tsx` 的 `__group` / `__section-heading` / `__label` / `__section-action` / `__group-list` / `__empty` 结构与样式，以及 `ProjectEntityRow` 的展开箭头（展开 `CollapseIcon` vs 收起 `ArrowRightIcon`）。缩进：项目内会话 `padding-left: 32px`。

- [ ] **Step 1:** 删掉 `AssistantPicker` 组件（及其内部 `AssistantAvatar`，若别处不用），侧栏顶部只保留「新会话」按钮 + 搜索框。

- [ ] **Step 2:** `SectionHeading` 小组件（标题 + hover 才出现的 `+`），照 `.conversation-sidebar__section-heading` / `__label` / `__section-action`（`opacity:0` → heading hover 时 `opacity:1`）。

- [ ] **Step 3:** `WorkspaceGroup.tsx`：
  - 标题「工作区」+ `+`（hover 出现）→ 新建工作区（复用 `stores/projects.ts` 的 `create(name)`；名字用 `window.prompt`，与 `ProjectPicker` 现有做法一致）
  - 列出**非默认**工作区；每行文件夹图标 + 名字 + 展开箭头；展开 → 该项目下的会话（缩进），空则「暂无会话」
  - 点工作区行的**名字区域**= 展开/收起；另给一个「设为当前工作区」的入口（或点行即 `setCurrent` 并展开）——你判断哪种更顺手，在回报里说明
  - 默认工作区 = `dir_name === 'default'` 的那个，**不在此组渲染**

- [ ] **Step 4:** 「会话」组：标题「会话」+ `+`（新建会话到默认工作区）→ 列**默认工作区**下的会话。没有默认工作区时（新用户还没触发迁移）显示「暂无会话」。

- [ ] **Step 5:** `SessionList.tsx` 支持两种用法：整体（现有，保留以兼容搜索）与「给定一组会话 + 缩进」。搜索命中时应跨全部会话显示（搜索态下退化为现有扁平列表）。

- [ ] **Step 6:** 会话归属：`session.project_id` 为空（C5 之前的旧会话）的，归到默认工作区。

- [ ] **Step 7: 验证**：`npm run build && npm run lint`；`npm run build` 后真机由我验

- [ ] **Step 8: Commit** — `feat(frontend): 侧栏改为工作区/会话两级层级`

---

## Task D4: 前端——输入框卡片按 jiuwen 空态形态重做

**Files:** Create `components/chat/WorkspacePicker.tsx`；Modify `components/chat/Composer.tsx`；Delete `components/chat/ProjectPicker.tsx`

**照抄**：`InputArea.tsx:2703-2720` 的 `chat-input-frame > chat-input-container > (chat-input-body + chat-work-context-wrapper)`，以及 `ChatPanel.css:136-170` 的 `--work-home` 变体与 `2223-2256` 的项目行。

- [ ] **Step 1:** `WorkspacePicker.tsx`（由 `ProjectPicker` 改）：底部那一行——文件夹图标 + **当前工作区名，未选时显示「请选择工作区」** + chevron；点开菜单：工作区列表（当前项打勾）+ 分隔线 +「新建工作区」。未选时点开会选中默认工作区。

- [ ] **Step 2:** `Composer.tsx` 空态结构改为：

```
<div 外层灰卡>                       ← bg 用 --sa 里接近 action-secondary 的 token；padding 4px；radius 24
  <div 内层白卡>                      ← radius 20；border；内含 textarea + 底部工具栏
    <textarea … />
    <div 工具栏> [+] [专家 chip] … [模型 ▾] [发送] </div>
  </div>
  <div 工作区行> <WorkspacePicker /> </div>   ← 圆角 0 0 24 24，与外层卡同底色
</div>
```

- [ ] **Step 3:** 有消息时**退回单层白卡**、不渲染工作区行（jiuwen 的 `showWorkContextRow` 只在空态为 true）。

- [ ] **Step 4:** `stores/projects.ts` 的 `currentId` 允许为 `null`（未选）；`load()` 不再自动选中首个。发消息建会话时若为 null → 不带 `project_id`（后端 `resolve_active_project` 会回落到默认项目）。

- [ ] **Step 5: 验证**：`npm run build && npm run lint`；真机由我验

- [ ] **Step 6: Commit** — `feat(frontend): 输入框空态卡片与工作区行重做`

---

# 验收

- [ ] `cd apps/web/backend && conda run -n synlysagent --no-capture-output python -m pytest -q`
- [ ] `cd packages/synlys-harness && conda run -n synlysagent --no-capture-output python -m pytest -q`
- [ ] `cd apps/web/frontend && npm run build && npm run lint`
- [ ] playwright **有头模式**逐场景截图自查：
  1. 侧栏：无助手卡；「工作区」组 + 「会话」组；默认工作区不在工作区组里
  2. 新建工作区 → 出现在工作区组 → 展开显示「暂无会话」
  3. 「新会话」→ 归入默认工作区，出现在「会话」组
  4. 输入框空态：外层灰卡包住内白卡 + 底部「请选择工作区」行；有消息后退回单层卡
  5. `+ → 专家 → 不使用专家` → chip 消失；发消息 → 回复正常（用的是平台默认提示词，无专家 persona）
  6. 控制台 0 error

---

## 自检

**需求覆盖**

| 用户要求 | 覆盖任务 |
|---|---|
| 左侧栏助手选择去掉 | D3 Step 1 |
| 可以不勾选专家助手 | D1（后端）、D2（前端） |
| 侧栏层级：工作区 + 会话 | D3 |
| 工作区可以新建工作区 | D3 Step 3 |
| 会话层级用默认工作区 | D3 Step 4 |
| 对话展示在对应层级下 | D3 Step 3/6 |
| 输入框样式参考 jiuwen | D4 |
| 工作区选择外层背景框包住整个输入框 | D4 Step 2 |
| 未选工作区显示「请选择工作区」，未选则默认 | D4 Step 1/4 |
| 定时任务不搬 | 不做 |

**有意省略**
- 侧栏折叠按钮（jiuwen 右上角那个）：本项目已有 `<900px` 的 overlay 抽屉，不另做折叠态。
- 「置顶」组（jiuwen 有）：本轮不做。
- 工作区重命名/删除的侧栏入口：本轮不做（API 已就绪）。
- 有消息时切换工作区：不做（会话创建时绑定，与 jiuwen 一致）。
