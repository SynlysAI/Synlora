# 能力中心「市场 / 我的」前端改造实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把用户侧能力中心（`/capabilities`）改造成「市场 / 我的」两栏，支持安装、启用/停用、卸载，以及用户自建技能与专家的创建/编辑/删除。

**Architecture:** 复用现有页面骨架（`PageTopBar` + 行卡片列表 + `Modal` 表单 + `toast`）。新增一个「我的能力」store（`stores/myCapabilities.ts`）承载合并视图；市场 store（`stores/catalog.ts`）改走 `/api/v1/market/{kind}` 并按 `enabled` 渲染动作。视觉与交互照抄 jiuwen `SkillPanel/index.tsx` 的页签与子页签结构、DSH 的行卡片与按钮样式。

**Tech Stack:** React 19 + TypeScript + Tailwind 4 + Zustand；构建 `npm run build`。

**规格来源:** `docs/superpowers/specs/2026-09-15-synlysagent-user-content-and-data-layout-design.md`
**后端前置:** `docs/superpowers/plans/2026-09-15-synlysagent-10-user-content-backend.md` 必须已完成（`/api/v1/market/{kind}`、`/api/v1/me/*`、`PUT /api/v1/me/capabilities/...` 已存在）。

**验证约定（项目既定）:** 前端**不写单测**；每个任务以 `npm run build` 通过为完成标准，真机验证由用户在浏览器自测。所有命令在 `apps/web/frontend` 下执行。

**参考实现（动手前先读）:**
- jiuwenswarm `channels/web/frontend/src/components/SkillPanel/index.tsx`（`activeTab: 'my' | 'marketplace'`、`mySkillsSubTab: 'all' | 'enabled' | 'disabled' | 'builtin'`）
- 本项目 `components/admin/PluginsAdmin.tsx`（行卡片 + 模态 + toast + 刷新）

---

### Task 1: 类型与市场 store 改走新端点

**Files:**
- Modify: `apps/web/frontend/src/types.ts:115-127`
- Modify: `apps/web/frontend/src/stores/catalog.ts`

- [ ] **Step 1: 扩展 `CatalogItem` 并新增「我的能力」类型**

`types.ts` 中 `CatalogItem` 替换为：

```ts
export interface CatalogItem {
  kind: 'expert' | 'skill' | 'plugin'
  id: string
  name: string
  description: string
  source: 'builtin'
  visibility: 'public' | 'hidden'
  default_enabled: boolean
  installed: boolean
  /** 已安装时是否处于启用态（停用 = 已装但不注入运行期）。 */
  enabled: boolean
  visible: boolean
  /** 仅插件行返回：安装时可填的配置字段 */
  config_schema?: PluginConfigField[]
}

/** 「我的」列表条目：自建（mine）或已安装的内置（installed）。 */
export interface MyCapability {
  kind: 'skill' | 'expert' | 'plugin'
  id: string
  name: string
  description: string
  source: 'mine' | 'installed'
  enabled: boolean
  builtin: boolean
  /** 管理员已下架（仅 installed 条目可能出现）：不可启用。 */
  revoked?: boolean
}
```

- [ ] **Step 2: 市场 store 改端点并按 kind 分组拉取**

`stores/catalog.ts` 整体替换为：

```ts
/**
 * 用户侧能力中心-市场 store：按类型拉取可安装条目 + 安装/启用/停用/卸载。
 *
 * 走 `/api/v1/market/{kind}`（普通用户视角，不出现 hidden 条目）；安装与开关统一
 * 走 `PUT /api/v1/me/capabilities/{kind}/{id}`，插件安装时仍按 config_schema 先填配置。
 * 任何写操作成功后重拉市场列表（不做乐观更新，与 PluginsAdmin 策略开关一致）。
 */
import { create } from 'zustand'
import type { CatalogItem } from '@/types'
import { api } from '@/api/client'

/** 市场按类型分组存放（三类分别拉取，避免一次请求混排后再在前端切分）。 */
type KindMap = Record<CatalogItem['kind'], CatalogItem[]>

interface CatalogState {
  /** 当前用户可见的市场条目（三类分组）。 */
  byKind: KindMap
  /** 三类是否均完成首次成功加载。 */
  loaded: boolean
  /** 拉取市场三类条目。 */
  loadMarket: () => Promise<void>
  /** 安装条目（插件可带个人配置），成功后重拉。 */
  install: (kind: CatalogItem['kind'], itemId: string, config?: Record<string, string>) => Promise<void>
  /** 卸载条目，成功后重拉。 */
  uninstall: (kind: CatalogItem['kind'], itemId: string) => Promise<void>
  /** 启用/停用已安装条目，成功后重拉。 */
  setEnabled: (kind: CatalogItem['kind'], itemId: string, enabled: boolean) => Promise<void>
}

const EMPTY: KindMap = { expert: [], skill: [], plugin: [] }
const KINDS: CatalogItem['kind'][] = ['expert', 'skill', 'plugin']

export const useCatalogStore = create<CatalogState>((set, get) => ({
  byKind: EMPTY,
  loaded: false,

  loadMarket: async () => {
    const lists = await Promise.all(
      KINDS.map((kind) => api<CatalogItem[]>(`/api/v1/market/${kind}`)),
    )
    const byKind = { ...EMPTY }
    KINDS.forEach((kind, index) => {
      byKind[kind] = lists[index]
    })
    set({ byKind, loaded: true })
  },

  install: async (kind, itemId, config) => {
    await api(`/api/v1/me/capabilities/${kind}/${encodeURIComponent(itemId)}`, {
      method: 'PUT',
      body: { installed: true },
    })
    // 插件：安装时若有个人配置，紧接着写配置（后端安装端点已按 schema 校验必填）
    if (kind === 'plugin' && config && Object.keys(config).length > 0) {
      await api(`/api/v1/catalog/${kind}/${encodeURIComponent(itemId)}/install`, {
        method: 'POST',
        body: { config },
      })
    }
    await get().loadMarket()
  },

  uninstall: async (kind, itemId) => {
    await api(`/api/v1/me/capabilities/${kind}/${encodeURIComponent(itemId)}`, {
      method: 'PUT',
      body: { installed: false },
    })
    await get().loadMarket()
  },

  setEnabled: async (kind, itemId, enabled) => {
    await api(`/api/v1/me/capabilities/${kind}/${encodeURIComponent(itemId)}`, {
      method: 'PUT',
      body: { enabled },
    })
    await get().loadMarket()
  },
}))
```

- [ ] **Step 3: 同步改名点，保持构建通过**

`CapabilityCenter.tsx` 只做"改名"级别的同步（动作 UI 留到 Task 3）：

1) 组件顶部三个取值改为：

```tsx
  const byKind = useCatalogStore((s) => s.byKind)
  const loaded = useCatalogStore((s) => s.loaded)
  const loadMarket = useCatalogStore((s) => s.loadMarket)
```

2) `useEffect` 与错误文案里的 `loadCatalog` 一律改名 `loadMarket`。

3) 分组循环里 `const rows = items.filter((i) => i.kind === group.kind)` 改为：

```tsx
              const rows = byKind[group.kind]
```

Run: `npm run build`
Expected: 构建成功。

- [ ] **Step 4: 提交**

```bash
git add apps/web/frontend/src/types.ts apps/web/frontend/src/stores/catalog.ts
git commit -m "refactor: 市场 store 改走 market 端点并支持启用态"
```

---

### Task 2: 我的能力 store

**Files:**
- Create: `apps/web/frontend/src/stores/myCapabilities.ts`

（`stores/index.ts` 是部分 store 的 barrel，`catalog.ts` 并不在其中——页面级 store 走直接
import 的既有惯例，故本 store 同样不进 barrel。）

- [ ] **Step 1: 新建 store**

创建 `apps/web/frontend/src/stores/myCapabilities.ts`：

```ts
/**
 * 「我的能力」store：自建（技能/专家）∪ 已安装的内置（技能/专家/插件）合并视图。
 *
 * 数据来源三处（后端各自负责归属与可见性）：
 * - `GET /api/v1/me/skills`  自建技能 + 已装内置技能
 * - `GET /api/v1/me/experts` 自建专家 + 已装内置专家
 * - `GET /api/v1/market/plugin` 过滤 installed → 我的插件
 * 写操作复用市场 store 的开关语义（`PUT /me/capabilities/...`）与 `/me/skills|experts` 的 CRUD。
 */
import { create } from 'zustand'
import type { CatalogItem, MyCapability } from '@/types'
import { api } from '@/api/client'

/** 后端 /me/skills 与 /me/experts 的行形状。 */
interface MyRow {
  name: string
  description: string
  source: 'mine' | 'installed'
  enabled: boolean
  builtin: boolean
  revoked?: boolean
}
interface MyExpertRow {
  id: string
  name: string
  avatar: string
  description: string
  source: 'mine' | 'installed'
  enabled: boolean
  builtin: boolean
}
/** 自建技能/专家的提交体。 */
export interface SkillDraft {
  name: string
  description: string
  content: string
}
export interface ExpertDraft {
  name: string
  avatar: string
  description: string
  system_prompt: string
  tool_whitelist: string[]
}

interface MyCapabilitiesState {
  items: MyCapability[]
  loaded: boolean
  loadMine: () => Promise<void>
  createSkill: (draft: SkillDraft) => Promise<void>
  updateSkill: (name: string, draft: Omit<SkillDraft, 'name'>) => Promise<void>
  deleteSkill: (name: string) => Promise<void>
  createExpert: (draft: ExpertDraft) => Promise<void>
  updateExpert: (id: string, draft: ExpertDraft) => Promise<void>
  deleteExpert: (id: string) => Promise<void>
}

export const useMyCapabilitiesStore = create<MyCapabilitiesState>((set, get) => ({
  items: [],
  loaded: false,

  loadMine: async () => {
    const [skills, experts, plugins] = await Promise.all([
      api<MyRow[]>('/api/v1/me/skills'),
      api<MyExpertRow[]>('/api/v1/me/experts'),
      api<CatalogItem[]>('/api/v1/market/plugin'),
    ])
    const items: MyCapability[] = [
      ...skills.map((s) => ({
        kind: 'skill' as const,
        id: s.name,
        name: s.name,
        description: s.description,
        source: s.source,
        enabled: s.enabled,
        builtin: s.builtin,
        revoked: s.revoked,
      })),
      ...experts.map((e) => ({
        kind: 'expert' as const,
        id: e.id,
        name: e.name,
        description: e.description,
        source: e.source,
        enabled: e.enabled,
        builtin: e.builtin,
      })),
      ...plugins
        .filter((p) => p.installed)
        .map((p) => ({
          kind: 'plugin' as const,
          id: p.id,
          name: p.name,
          description: p.description,
          source: 'installed' as const,
          enabled: p.enabled,
          builtin: true,
        })),
    ]
    set({ items, loaded: true })
  },

  createSkill: async (draft) => {
    await api('/api/v1/me/skills', { method: 'POST', body: draft })
    await get().loadMine()
  },

  updateSkill: async (name, draft) => {
    await api(`/api/v1/me/skills/${encodeURIComponent(name)}`, {
      method: 'PATCH',
      body: draft,
    })
    await get().loadMine()
  },

  deleteSkill: async (name) => {
    await api(`/api/v1/me/skills/${encodeURIComponent(name)}`, { method: 'DELETE' })
    await get().loadMine()
  },

  createExpert: async (draft) => {
    await api('/api/v1/me/experts', { method: 'POST', body: draft })
    await get().loadMine()
  },

  updateExpert: async (id, draft) => {
    await api(`/api/v1/me/experts/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: draft,
    })
    await get().loadMine()
  },

  deleteExpert: async (id) => {
    await api(`/api/v1/me/experts/${encodeURIComponent(id)}`, { method: 'DELETE' })
    await get().loadMine()
  },
}))
```

- [ ] **Step 2: 构建验证**

Run: `npm run build`
Expected: 除 `CapabilityCenter.tsx` 的既有报错外无新增错误。

- [ ] **Step 3: 提交**

```bash
git add apps/web/frontend/src/stores/myCapabilities.ts
git commit -m "feat: 新增我的能力 store（自建 ∪ 已装）"
```

---

### Task 3: 市场栏（页签 + 动作改造）

**Files:**
- Modify: `apps/web/frontend/src/components/catalog/CapabilityCenter.tsx`

- [ ] **Step 1: 加顶层两栏页签并改造市场渲染**

`CapabilityCenter.tsx` 中：

1) 组件内新增页签态：

```tsx
  /** 顶层视角：市场（可安装的源）/ 我的（已拥有）。 */
  const [tab, setTab] = useState<'market' | 'mine'>('market')
```

2) store 取值改为：

```tsx
  const byKind = useCatalogStore((s) => s.byKind)
  const loaded = useCatalogStore((s) => s.loaded)
  const loadMarket = useCatalogStore((s) => s.loadMarket)
  const setEnabled = useCatalogStore((s) => s.setEnabled)
```

（`install`/`uninstall` 保持原样取自 store。）

3) 页头下方插入页签（照 DSH 分段按钮：选中项底色 `--sa-alias-bg-layer-1` + 描边）：

```tsx
            {/* 顶层两栏：市场 / 我的（照 jiuwen SkillPanel 的 activeTab） */}
            <div className="flex items-center gap-1">
              {([['market', '市场'], ['mine', '我的']] as const).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setTab(key)}
                  className={
                    tab === key
                      ? 'rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] px-3 py-[5px] text-[13px] font-medium text-[var(--sa-alias-label-primary)]'
                      : 'rounded-[var(--sa-radius-md)] border border-transparent px-3 py-[5px] text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]'
                  }
                >
                  {label}
                </button>
              ))}
            </div>
```

4) 分组渲染的 `items.filter(...)` 改为 `byKind[group.kind]`，并把行内动作区替换为：

```tsx
                        <div className="flex shrink-0 items-center gap-2.5">
                          {item.installed ? (
                            <>
                              {item.enabled ? <GrayBadge>已启用</GrayBadge> : <GrayBadge>已停用</GrayBadge>}
                              <button
                                type="button"
                                onClick={() => void handleToggle(item)}
                                className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]"
                              >
                                {item.enabled ? '停用' : '启用'}
                              </button>
                              <button
                                type="button"
                                onClick={() => void handleUninstall(item)}
                                className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]"
                              >
                                卸载
                              </button>
                            </>
                          ) : (
                            <button
                              type="button"
                              onClick={() => void handleInstall(item)}
                              className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]"
                            >
                              安装
                            </button>
                          )}
                        </div>
```

同时把行内的 `<GrayBadge>内置</GrayBadge>` 保留，删掉 `{item.installed && !item.default_enabled && <GrayBadge>已安装</GrayBadge>}`
（状态改由 `已启用/已停用` 徽标表达），并删掉 `item.default_enabled ? <GrayBadge>默认可用</GrayBadge> : ...` 分支。

5) 新增切换处理器（放在 `handleUninstall` 旁）：

```tsx
  /** 启用/停用：改 user_capabilities.enabled，成功后 store 重拉。 */
  const handleToggle = async (item: CatalogItem) => {
    try {
      await setEnabled(item.kind, item.id, !item.enabled)
      toast('success', item.enabled ? `已停用 ${item.name}` : `已启用 ${item.name}`)
    } catch (err) {
      toast('error', errorText(err))
    }
  }
```

6) `useEffect` 依赖的 `loadCatalog` 改名 `loadMarket`，并把市场区块包进
`{tab === 'market' && ( ... )}`；`{tab === 'mine' && <MinePanel />}` 在 Task 4 补上。

- [ ] **Step 2: 构建验证**

Run: `npm run build`
Expected: 构建成功。

- [ ] **Step 3: 提交**

```bash
git add apps/web/frontend/src/components/catalog/CapabilityCenter.tsx
git commit -m "feat: 能力中心加市场/我的页签，市场行支持启用停用"
```

---

### Task 4: 我的栏（子页签 + 自建技能/专家表单）

**Files:**
- Create: `apps/web/frontend/src/components/catalog/MinePanel.tsx`
- Modify: `apps/web/frontend/src/components/catalog/CapabilityCenter.tsx`（挂载子面板）
- Modify: `apps/web/frontend/src/components/catalog/index.ts`（若需导出）

- [ ] **Step 1: 新建 MyPanel 组件**

创建 `components/catalog/MinePanel.tsx`：

```tsx
/**
 * 「我的」面板：自建 + 已安装的能力（技能/专家/插件），带子页签过滤。
 *
 * 结构照 jiuwen `SkillPanel/index.tsx`：子页签 全部/已启用/已停用/内置；
 * 自建条目可编辑/删除，内置条目只能启用/停用/卸载（规格 D5：内置不可定制）。
 */
import { useEffect, useState, type FormEvent } from 'react'
import { FormError, GrayBadge, Modal } from '@/components/admin/shared'
import { errorText, inputClass, labelClass, primaryButtonClass, secondaryButtonClass } from '@/components/admin/form'
import { toast } from '@/stores/toasts'
import { useCatalogStore } from '@/stores/catalog'
import { useMyCapabilitiesStore, type ExpertDraft, type SkillDraft } from '@/stores/myCapabilities'
import type { MyCapability } from '@/types'

type SubTab = 'all' | 'enabled' | 'disabled' | 'builtin'

const SUB_TABS: Array<{ key: SubTab; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'enabled', label: '已启用' },
  { key: 'disabled', label: '已停用' },
  { key: 'builtin', label: '内置' },
]

const KIND_LABEL: Record<MyCapability['kind'], string> = {
  skill: '技能',
  expert: '专家',
  plugin: '插件',
}

/** 技能编辑模态（自建：新建与编辑共用）。 */
function SkillModal({
  initial,
  onClose,
}: {
  initial: MyCapability | null
  onClose: (changed: boolean) => void
}) {
  const createSkill = useMyCapabilitiesStore((s) => s.createSkill)
  const updateSkill = useMyCapabilitiesStore((s) => s.updateSkill)
  const editing = initial !== null
  const [name, setName] = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [content, setContent] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (saving) return
    setSaving(true)
    setError('')
    try {
      const draft: SkillDraft = { name: name.trim(), description: description.trim(), content }
      if (editing) await updateSkill(initial.id, { description: draft.description, content: draft.content })
      else await createSkill(draft)
      onClose(true)
    } catch (err) {
      setError(errorText(err))
      setSaving(false)
    }
  }

  return (
    <Modal title={editing ? `编辑技能 ${initial?.name}` : '新建技能'} onClose={() => onClose(false)}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        {!editing && (
          <label className={labelClass}>
            技能名（kebab-case）
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my-skill"
              required
              autoFocus
              className={inputClass}
            />
          </label>
        )}
        <label className={labelClass}>
          描述（做什么 + 何时用）
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            required
            autoFocus={editing}
            className={inputClass}
          />
        </label>
        <label className={labelClass}>
          正文（Markdown，不含 frontmatter）
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={10}
            required
            className={`${inputClass} resize-y font-mono`}
          />
        </label>
        {error && <FormError>{error}</FormError>}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={() => onClose(false)} className={secondaryButtonClass}>取消</button>
          <button type="submit" disabled={saving} className={primaryButtonClass}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

/** 专家编辑模态（自建：新建与编辑共用）。 */
function ExpertModal({
  initial,
  onClose,
}: {
  initial: MyCapability | null
  onClose: (changed: boolean) => void
}) {
  const createExpert = useMyCapabilitiesStore((s) => s.createExpert)
  const updateExpert = useMyCapabilitiesStore((s) => s.updateExpert)
  const editing = initial !== null
  const [name, setName] = useState(initial?.name ?? '')
  const [avatar, setAvatar] = useState('')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [tools, setTools] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const draft = (): ExpertDraft => ({
    name: name.trim(),
    avatar: avatar.trim(),
    description: description.trim(),
    system_prompt: systemPrompt,
    tool_whitelist: tools.split(',').map((t) => t.trim()).filter(Boolean),
  })

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (saving) return
    setSaving(true)
    setError('')
    try {
      if (editing) await updateExpert(initial.id, draft())
      else await createExpert(draft())
      onClose(true)
    } catch (err) {
      setError(errorText(err))
      setSaving(false)
    }
  }

  return (
    <Modal title={editing ? `编辑专家 ${initial?.name}` : '新建专家'} onClose={() => onClose(false)}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <label className={labelClass}>
          名称
          <input value={name} onChange={(e) => setName(e.target.value)} required autoFocus className={inputClass} />
        </label>
        <label className={labelClass}>
          头像 emoji（可空）
          <input value={avatar} onChange={(e) => setAvatar(e.target.value)} className={inputClass} />
        </label>
        <label className={labelClass}>
          描述
          <input value={description} onChange={(e) => setDescription(e.target.value)} className={inputClass} />
        </label>
        <label className={labelClass}>
          人设提示词
          <textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            rows={6}
            required
            className={`${inputClass} resize-y`}
          />
        </label>
        <label className={labelClass}>
          可用工具（逗号分隔，留空 = 全部内置工具）
          <input value={tools} onChange={(e) => setTools(e.target.value)} placeholder="python.run, file.read" className={inputClass} />
        </label>
        {error && <FormError>{error}</FormError>}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={() => onClose(false)} className={secondaryButtonClass}>取消</button>
          <button type="submit" disabled={saving} className={primaryButtonClass}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

/** 「我的」面板。 */
export default function MinePanel() {
  const items = useMyCapabilitiesStore((s) => s.items)
  const loaded = useMyCapabilitiesStore((s) => s.loaded)
  const loadMine = useMyCapabilitiesStore((s) => s.loadMine)
  const deleteSkill = useMyCapabilitiesStore((s) => s.deleteSkill)
  const deleteExpert = useMyCapabilitiesStore((s) => s.deleteExpert)
  const setEnabled = useCatalogStore((s) => s.setEnabled)
  const uninstall = useCatalogStore((s) => s.uninstall)
  const [sub, setSub] = useState<SubTab>('enabled')
  const [editingSkill, setEditingSkill] = useState<MyCapability | null | undefined>(undefined)
  const [editingExpert, setEditingExpert] = useState<MyCapability | null | undefined>(undefined)

  useEffect(() => {
    loadMine().catch((err) => toast('error', `加载我的能力失败：${errorText(err)}`))
  }, [loadMine])

  const rows = items.filter((item) => {
    if (sub === 'enabled') return item.enabled
    if (sub === 'disabled') return !item.enabled
    if (sub === 'builtin') return item.builtin
    return true
  })

  const handleToggle = async (item: MyCapability) => {
    try {
      await setEnabled(item.kind, item.id, !item.enabled)
      toast('success', item.enabled ? `已停用 ${item.name}` : `已启用 ${item.name}`)
      await loadMine()
    } catch (err) {
      toast('error', errorText(err))
    }
  }

  const handleRemove = async (item: MyCapability) => {
    try {
      if (item.source === 'mine' && item.kind === 'skill') await deleteSkill(item.id)
      else if (item.source === 'mine' && item.kind === 'expert') await deleteExpert(item.id)
      else await uninstall(item.kind, item.id)
      await loadMine()
      toast('success', `已删除 ${item.name}`)
    } catch (err) {
      toast('error', errorText(err))
    }
  }

  return (
    <div className="flex flex-col gap-3">
      {/* 工具行：新建入口 + 子页签 */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-1">
          {SUB_TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setSub(t.key)}
              className={
                sub === t.key
                  ? 'rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-interactive-bg-hover)] px-2.5 py-[3px] text-[12px] text-[var(--sa-alias-label-primary)]'
                  : 'rounded-[var(--sa-radius-sm)] px-2.5 py-[3px] text-[12px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]'
              }
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-3">
          <button type="button" onClick={() => setEditingSkill(null)} className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]">
            + 新建技能
          </button>
          <button type="button" onClick={() => setEditingExpert(null)} className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]">
            + 新建专家
          </button>
        </div>
      </div>

      <div className="overflow-hidden rounded-[var(--sa-radius-lg)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)]">
        {rows.map((item) => (
          <div
            key={`${item.kind}:${item.id}`}
            className="flex items-center gap-3 border-b border-[var(--sa-alias-border-l1)] px-4 py-3 last:border-b-0 hover:bg-[var(--sa-alias-interactive-bg-hover)]"
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate font-mono text-[14px] font-medium">{item.name}</span>
                <GrayBadge>{KIND_LABEL[item.kind]}</GrayBadge>
                <GrayBadge>{item.source === 'mine' ? '自建' : '已安装'}</GrayBadge>
                {item.revoked && <GrayBadge>已被管理员下架</GrayBadge>}
              </div>
              <div className="truncate text-[13px] text-[var(--sa-alias-label-secondary)]" title={item.description}>
                {item.description || '无描述'}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2.5">
              {item.enabled ? <GrayBadge>已启用</GrayBadge> : <GrayBadge>已停用</GrayBadge>}
              {!item.revoked && (
                <button type="button" onClick={() => void handleToggle(item)} className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]">
                  {item.enabled ? '停用' : '启用'}
                </button>
              )}
              {item.source === 'mine' && item.kind === 'skill' && (
                <button type="button" onClick={() => setEditingSkill(item)} className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]">
                  编辑
                </button>
              )}
              {item.source === 'mine' && item.kind === 'expert' && (
                <button type="button" onClick={() => setEditingExpert(item)} className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]">
                  编辑
                </button>
              )}
              <button type="button" onClick={() => void handleRemove(item)} className="text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]">
                {item.source === 'mine' ? '删除' : '卸载'}
              </button>
            </div>
          </div>
        ))}
        {loaded && rows.length === 0 && (
          <div className="px-4 py-10 text-center text-[13px] text-[var(--sa-alias-label-caption)]">
            还没有技能或专家，去「市场」安装，或点右上角自己创建一个
          </div>
        )}
      </div>

      {editingSkill !== undefined && (
        <SkillModal
          initial={editingSkill}
          onClose={(changed) => {
            setEditingSkill(undefined)
            if (changed) {
              toast('success', '已保存技能')
              void loadMine()
            }
          }}
        />
      )}
      {editingExpert !== undefined && (
        <ExpertModal
          initial={editingExpert}
          onClose={(changed) => {
            setEditingExpert(undefined)
            if (changed) {
              toast('success', '已保存专家')
              void loadMine()
            }
          }}
        />
      )}
    </div>
  )
}
```

- [ ] **Step 2: 挂到能力中心**

`CapabilityCenter.tsx` 中 import 该组件，并把 `{tab === 'mine' && <MinePanel />}` 放在市场区块之后：

```tsx
import MinePanel from './MinePanel'
```

```tsx
            {tab === 'mine' && <MinePanel />}
```

- [ ] **Step 3: 构建验证**

Run: `npm run build`
Expected: 构建成功（`dist/` 产物更新）。

- [ ] **Step 4: 真机自测清单（交付给用户执行）**

启动后端（`cd apps/web/backend && python run_uvicorn.py`）后访问 `/capabilities`：

1. 「市场」装一个技能 → 切到「我的 · 已启用」能看到它，徽标为`自建/已安装`+`已启用`。
2. 点「停用」→ 该条进「已停用」；回对话发一条消息，技能列表里不应出现它。
3. 「+ 新建技能」→ 建一个与内置同名的技能 → 报错文案为"与内置技能同名，请换一个名字"。
4. 「+ 新建专家」→ 建完在「我的」可见；新会话的专家选择器里能选到它。
5. 自建技能点「编辑」改描述 → 保存后列表描述更新；点「删除」→ 条目消失且磁盘目录被删。

- [ ] **Step 5: 提交**

```bash
git add apps/web/frontend/src/components/catalog/
git commit -m "feat: 我的能力面板（子页签 + 自建技能/专家增删改）"
```

---

### Task 5: 清理旧端点用法与文档同步

**Files:**
- Modify: `apps/web/backend/app/catalog/api.py`（删除已无调用方的 `GET /api/v1/catalog`）
- Modify: `apps/web/frontend/src/components/admin/`（管理页若引用 `stores/catalog` 需同步）
- Modify: `CLAUDE.md`、`apps/web/frontend/package.json`（版本号）

- [ ] **Step 1: 确认旧端点无调用方**

Run: 在本仓库搜索 `/api/v1/catalog`（前端）与 `store.catalog`/`stores/catalog` 引用：
`grep -rn "api/v1/catalog" apps/web/frontend/src` 与 `grep -rn "stores/catalog" apps/web/frontend/src`
Expected: 只剩管理页可能引用（管理页走 `/admin/catalog`，属另一端点）。
若 `GET /api/v1/catalog` 确无前端调用方，从 `app/catalog/api.py` 删除该路由函数 `list_catalog`。

- [ ] **Step 2: 构建与后端测试双验证**

Run: `npm run build`（在 `apps/web/frontend`）
Run: `cd apps/web/backend && conda run -n synlysagent python -m pytest -v`
Expected: 均通过。

- [ ] **Step 3: 更新 CLAUDE.md 与版本**

- `CLAUDE.md` 的「能力目录（市场）」一节补一句：

```markdown
- 用户侧能力中心为「市场 / 我的」两栏（`/capabilities`）：市场装/卸、我的启停与自建技能/专家；内置条目不可编辑（想定制请自建换名）
```

- `apps/web/frontend/package.json` 的 `version` 与后端 `app/version.py` 保持同一语义化口径（后端计划 Task 15 已升次版本，此处同步前端）。

- [ ] **Step 4: 提交**

```bash
git add CLAUDE.md apps/web/frontend/package.json apps/web/backend/app/catalog/api.py
git commit -m "chore: 清理旧市场端点并同步文档与版本"
```

---

## 完成后

- 前端交付标准：`npm run build` 通过；真机自测清单（Task 4 Step 4）由用户执行。
- 全量验收对照规格第 11 节 1–6 条，其中 1–4、6 需真实账号验证。
