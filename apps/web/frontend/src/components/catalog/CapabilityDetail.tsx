/**
 * 能力详情页（/capabilities/<kind>/<id>）。
 *
 * 结构照 jiuwenswarm `AgentManagementPanel/DefinitionDetailPage.tsx`：
 * 返回按钮 → header（大头像 + 名称 + 徽标行 + 右侧动作按钮组）→ 限宽 body。
 * 不做 jiuwen 的「文件」tab（我们一个技能一份 SKILL.md、一个专家一份 json）。
 *
 * 动作按 origin 四态分支：
 * - market      → 安装（插件先弹配置框）
 * - installed   → 编辑配置（仅声明了 config_schema 的插件）+ 启用/停用 + 卸载
 * - mine        → 编辑 + 删除
 * - builtin     → 普通用户只读；管理员给「在管理后台编辑」跳转
 */
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { errorText } from '@/components/admin/form'
import { toast } from '@/stores/toasts'
import { useCatalogStore } from '@/stores/catalog'
import { useMyCapabilitiesStore } from '@/stores/myCapabilities'
import { useAuthStore } from '@/stores/auth'
import { useRouterStore } from '@/routing/router'
import type { CapabilityKind } from '@/routing/route'
import type { CapabilityDetail as Detail, CatalogItem, MyCapability } from '@/types'
import { CardBadge } from './CapabilityCard'
import ExpertEditor from './ExpertEditor'
import SkillFilePreview from './SkillFilePreview'
import {
  PluginEditConfigModal,
  PluginInstallModal,
  SkillModal,
} from './CapabilityModals'

/** 类型展示名。 */
const KIND_LABEL: Record<CapabilityKind, string> = {
  expert: '专家',
  skill: '技能',
  plugin: '插件',
}

/** 内置条目的管理后台落点。 */
const ADMIN_ROUTE: Record<CapabilityKind, { tab: 'skills' | 'assistants' | 'plugins' }> = {
  skill: { tab: 'skills' },
  expert: { tab: 'assistants' },
  plugin: { tab: 'plugins' },
}

/** 徽标行（类型 · 来源 · 启用态）。 */
function badgesOf(detail: Detail): ReactNode {
  const originLabel: Record<Detail['origin'], string> = {
    mine: '自建',
    installed: '已安装',
    builtin: '内置 · 全员可用',
    market: '未安装',
  }
  return (
    <>
      <CardBadge>{KIND_LABEL[detail.kind]}</CardBadge>
      <CardBadge>{originLabel[detail.origin]}</CardBadge>
      {detail.origin === 'installed' && !detail.revoked && (
        <CardBadge>{detail.enabled ? '已启用' : '已停用'}</CardBadge>
      )}
      {detail.revoked && <CardBadge>已被管理员下架</CardBadge>}
    </>
  )
}

/** 内容块：等宽 pre（技能正文 / 专家人设提示词共用）。 */
function CodeBlock({ text }: { text: string }) {
  return (
    <pre className="max-h-[480px] overflow-auto whitespace-pre-wrap break-words rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-specific-code-block)] p-4 font-mono text-[12.5px] leading-[20px] text-[var(--sa-alias-label-primary)]">
      {text}
    </pre>
  )
}

/** 小标题 + 内容块。 */
function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-[13px] font-medium text-[var(--sa-alias-label-secondary)]">{title}</h2>
      {children}
    </section>
  )
}

/** 标签池（插件附属清单用）。 */
function ChipList({ items }: { items: string[] }) {
  if (items.length === 0) {
    return <span className="text-[13px] text-[var(--sa-alias-label-caption)]">无</span>
  }
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((chip) => (
        <span
          key={chip}
          className="inline-flex h-6 items-center rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-interactive-bg-hover)] px-2.5 text-xs text-[var(--sa-alias-label-primary)]"
        >
          {chip}
        </span>
      ))}
    </div>
  )
}

/** 详情页动作按钮样式。 */
const actionClass =
  'h-8 rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-border-l2)] px-3 text-[13px] text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover)] hover:text-[var(--sa-alias-label-primary)] disabled:cursor-not-allowed disabled:opacity-50'
const primaryActionClass =
  'h-8 rounded-[var(--sa-radius-sm)] border border-[var(--sa-alias-button-primary-fill)] bg-[var(--sa-alias-button-primary-fill)] px-3 text-[13px] font-medium text-[var(--sa-alias-label-primary-foreground)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-button-primary-hover)] disabled:cursor-not-allowed disabled:opacity-50'

/** 能力详情页。 */
export default function CapabilityDetail({
  capabilityKind,
  itemId,
}: {
  capabilityKind: CapabilityKind
  itemId: string
}) {
  const loadDetail = useCatalogStore((s) => s.loadDetail)
  const install = useCatalogStore((s) => s.install)
  const uninstall = useCatalogStore((s) => s.uninstall)
  const setEnabled = useCatalogStore((s) => s.setEnabled)
  const deleteSkill = useMyCapabilitiesStore((s) => s.deleteSkill)
  const deleteExpert = useMyCapabilitiesStore((s) => s.deleteExpert)
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')

  const [detail, setDetail] = useState<Detail | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [skillTab, setSkillTab] = useState<'overview' | 'files'>('overview')
  const [editing, setEditing] = useState<MyCapability | null | undefined>(undefined)
  const [configuring, setConfiguring] = useState<CatalogItem | null>(null)
  // 插件个人配置编辑（详情页：非敏感字段预填自 detail.config，敏感字段留空保持原值）
  const [editingConfig, setEditingConfig] = useState(false)

  /** 拉取详情（写操作后也走它刷新）。 */
  const refresh = useCallback(async () => {
    setError('')
    try {
      setDetail(await loadDetail(capabilityKind, itemId))
    } catch (err) {
      setError(errorText(err))
    } finally {
      setLoading(false)
    }
  }, [loadDetail, capabilityKind, itemId])

  useEffect(() => {
    void refresh()
  }, [refresh])

  /** 返回列表（保留左导航，回到同类型的市场列表）。 */
  const goBack = () =>
    useRouterStore.getState().navigate({ kind: 'capabilities', capabilityKind })

  /**
   * 包装写操作：置忙 → 执行 → 刷新详情 → 提示；返回是否成功（删除后据此决定是否返回列表）。
   *
   * `refreshing: false` 用于「写完条目就消失、调用方随即跳走」的路径（删除、
   * 卸载已下架条目）：此时详情接口必 404，刷新会先把错误写进 state、闪一帧
   * 「条目不存在」才跳转，所以直接跳过。
   */
  const run = async (
    label: string,
    fn: () => Promise<void>,
    { refreshing = true }: { refreshing?: boolean } = {},
  ): Promise<boolean> => {
    setBusy(true)
    try {
      await fn()
      toast('success', label)
      if (refreshing) await refresh()
      return true
    } catch (err) {
      toast('error', errorText(err))
      return false
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return (
      <div className="mx-auto w-full max-w-[1400px] px-12 pt-8 pb-10 text-sm text-[var(--sa-alias-label-caption)]">
        加载中…
      </div>
    )
  }
  if (error || !detail) {
    return (
      <div className="mx-auto flex w-full max-w-[1400px] flex-col items-start gap-3 px-12 pt-8 pb-10">
        <button type="button" onClick={goBack} className={actionClass}>← 返回</button>
        <p className="text-[13px] text-[var(--sa-alias-state-error-primary)]">{error || '条目不存在'}</p>
        <button type="button" onClick={() => void refresh()} className={actionClass}>重试</button>
      </div>
    )
  }

  /** 详情对象转编辑弹窗需要的 MyCapability 形状。 */
  const asMyCapability: MyCapability = {
    kind: detail.kind,
    id: detail.id,
    name: detail.name,
    description: detail.description,
    source: detail.origin === 'mine' ? 'mine' : detail.origin === 'builtin' ? 'builtin' : 'installed',
    enabled: detail.enabled,
    builtin: detail.origin !== 'mine',
  }

  return (
    <div className="mx-auto w-full max-w-[1400px] px-12 pt-8 pb-10">
      <button
        type="button"
        onClick={goBack}
        className="mb-5 flex items-center gap-1.5 text-sm text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:text-[var(--sa-alias-label-primary)]"
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M6.5 3 3 8l3.5 5M13 8H3.2" />
        </svg>
        返回
      </button>

      {/* header：头像 + 名称 + 徽标 + 动作组 */}
      <header className="flex flex-wrap items-start gap-4 pb-6">
        <span
          aria-hidden="true"
          className="flex h-16 w-16 shrink-0 items-center justify-center rounded-[var(--sa-radius-md)] bg-[var(--sa-alias-state-business-tertiary)] text-[28px] font-bold text-[var(--sa-alias-link)]"
        >
          {detail.avatar || (detail.name.trim().slice(0, 1).toUpperCase() || '?')}
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <h1 className="min-w-0 truncate text-[18px] font-semibold text-[var(--sa-alias-label-primary)]">
            {detail.name}
          </h1>
          <div className="flex flex-wrap items-center gap-1.5">{badgesOf(detail)}</div>
        </div>

        {/* 动作按钮组（按 origin 分支） */}
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {detail.origin === 'market' && (
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                if (detail.config_schema && detail.config_schema.length > 0) {
                  setConfiguring({
                    kind: detail.kind, id: detail.id, name: detail.name,
                    description: detail.description, source: 'builtin',
                    visibility: 'public', default_enabled: false,
                    installed: false, enabled: false, visible: false,
                    config_schema: detail.config_schema,
                    config_ready_keys: detail.config_ready_keys,
                  })
                  return
                }
                void run(`已安装 ${detail.name}`, () => install(detail.kind, detail.id))
              }}
              className={primaryActionClass}
            >
              安装
            </button>
          )}

          {detail.origin === 'installed' && (
            <>
              {/* 被管理员下架的条目只保留卸载（启用一个已下架的能力没有意义） */}
              {!detail.revoked && (detail.config_schema?.length ?? 0) > 0 && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setEditingConfig(true)}
                  className={actionClass}
                >
                  编辑配置
                </button>
              )}
              {!detail.revoked && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void run(
                    detail.enabled ? `已停用 ${detail.name}` : `已启用 ${detail.name}`,
                    () => setEnabled(detail.kind, detail.id, !detail.enabled),
                  )}
                  className={actionClass}
                >
                  {detail.enabled ? '停用' : '启用'}
                </button>
              )}
              <button
                type="button"
                disabled={busy}
                onClick={() => void (async () => {
                  const revoked = detail.revoked
                  // 已下架条目卸载后详情必 404（hidden 且未安装）：既不刷新也不停留，
                  // 成功即退回列表，否则会闪一帧「条目不存在」的错误屏；失败留在原地看提示。
                  // 普通条目卸载后详情仍可读（origin 变 market、给出「安装」），留在原地不打断。
                  const ok = await run(`已卸载 ${detail.name}`, () => uninstall(detail.kind, detail.id), {
                    refreshing: !revoked,
                  })
                  if (ok && revoked) goBack()
                })()}
                className={actionClass}
              >
                卸载
              </button>
            </>
          )}

          {detail.origin === 'mine' && (
            <>
              <button
                type="button"
                disabled={busy}
                onClick={() => setEditing(asMyCapability)}
                className={actionClass}
              >
                编辑
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void (async () => {
                  const remove = detail.kind === 'skill'
                    ? () => deleteSkill(detail.id)
                    : () => deleteExpert(detail.id)
                  // 删除成功后条目已不存在（详情必 404）：跳过写后刷新以免闪错误屏，
                  // 成功即退回列表；失败则留在原地让用户看到提示
                  if (await run(`已删除 ${detail.name}`, remove, { refreshing: false })) goBack()
                })()}
                className={actionClass}
              >
                删除
              </button>
            </>
          )}

          {detail.origin === 'builtin' && (
            isAdmin ? (
              <button
                type="button"
                onClick={() => useRouterStore.getState().navigate({
                  kind: 'admin', tab: ADMIN_ROUTE[detail.kind].tab,
                })}
                className={actionClass}
              >
                在管理后台编辑
              </button>
            ) : (
              <span className="text-[13px] text-[var(--sa-alias-label-caption)]">自动可用</span>
            )
          )}
        </div>
      </header>

      <div className="flex flex-col gap-6">
        {detail.description && (
          <Section title="描述">
            <p className="text-[13px] leading-[22px] text-[var(--sa-alias-label-primary)]">
              {detail.description}
            </p>
          </Section>
        )}

        {detail.kind === 'expert' && (
          <>
            <Section title="人设提示词">
              <CodeBlock text={detail.system_prompt || '（空）'} />
            </Section>
            <Section title="可用工具">
              <ChipList items={detail.tool_whitelist ?? []} />
              {(detail.tool_whitelist ?? []).length === 0 && (
                <span className="text-xs text-[var(--sa-alias-label-caption)]">留空表示全部内置工具可用</span>
              )}
            </Section>
            <Section title="能力编排">
              <div className="flex flex-col gap-3">
                <div className="flex flex-wrap gap-2">
                  {(detail.skill_refs ?? []).length > 0 ? <ChipList items={detail.skill_refs ?? []} /> : <span className="text-xs text-[var(--sa-alias-label-caption)]">未绑定技能</span>}
                </div>
                <div className="flex flex-wrap gap-2">
                  {(detail.mcp_refs ?? []).length > 0 ? <ChipList items={detail.mcp_refs ?? []} /> : <span className="text-xs text-[var(--sa-alias-label-caption)]">未绑定 MCP</span>}
                </div>
              </div>
            </Section>
            {(detail.suggested_prompts ?? []).length > 0 && (
              <Section title="推荐问题">
                <div className="flex flex-col gap-2">
                  {(detail.suggested_prompts ?? []).map((prompt) => (
                    <div key={prompt} className="rounded-[var(--sa-radius-sm)] bg-[var(--sa-alias-interactive-bg-hover)] px-3 py-2 text-[13px] text-[var(--sa-alias-label-primary)]">{prompt}</div>
                  ))}
                </div>
              </Section>
            )}
          </>
        )}

        {detail.kind === 'skill' && (
          <>
            <div className="flex gap-5 border-b border-[var(--sa-alias-border-l2)]">
              {([['overview', '技能说明'], ['files', '文件']] as const).map(([key, label]) => (
                <button key={key} type="button" onClick={() => setSkillTab(key)} className={`border-b-2 px-1 pb-2 text-sm ${skillTab === key ? 'border-[var(--sa-alias-link)] font-medium' : 'border-transparent text-[var(--sa-alias-label-secondary)]'}`}>{label}</button>
              ))}
            </div>
            {skillTab === 'overview' ? (
              <Section title="技能正文（SKILL.md）"><CodeBlock text={detail.content || '（空）'} /></Section>
            ) : (
              <SkillFilePreview skillName={detail.name} files={detail.files ?? []} />
            )}
          </>
        )}

        {detail.kind === 'plugin' && (
          <>
            <Section title="配置字段">
              {(detail.config_schema ?? []).length === 0 ? (
                <span className="text-sm text-[var(--sa-alias-label-caption)]">该插件无需配置</span>
              ) : (
                <div className="flex flex-col gap-4">
                  {(detail.config_schema ?? []).map((field) => {
                    const ready = (detail.config_ready_keys ?? []).includes(field.key)
                    const marks = [
                      field.required ? '必填' : null,
                      ready ? '系统默认已就绪' : null,
                      // 个人已存值的敏感字段只给标记（明文不回显）；保存后刷新详情即更新
                      field.secret && (detail.secrets_set ?? {})[field.key] ? '已配置' : null,
                    ].filter(Boolean)
                    return (
                      <div key={field.key} className="flex flex-col gap-1">
                        {/* 字段名用与下方「自带技能」同款的标签，key 用等宽字紧随其后 */}
                        <div className="flex flex-wrap items-center gap-2">
                          <CardBadge>{field.label}</CardBadge>
                          <span className="font-mono text-xs text-[var(--sa-alias-label-caption)]">
                            {field.key}
                          </span>
                        </div>
                        {/* 标记与描述一律走次级文字：同一行里标签与暗字交替会让人分不清
                            哪是名称哪是说明（标签只用来表示名称） */}
                        {marks.length > 0 && (
                          <p className="text-xs text-[var(--sa-alias-label-secondary)]">
                            {marks.join(' · ')}
                          </p>
                        )}
                        {field.description && (
                          <p className="text-xs leading-[18px] text-[var(--sa-alias-label-caption)]">
                            {field.description}
                          </p>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </Section>
            <Section title="自带技能">
              <ChipList items={(detail.skills ?? []).map((s) => s.name)} />
            </Section>
            <Section title="播种专家">
              <ChipList items={(detail.experts ?? []).map((e) => e.name)} />
            </Section>
            <Section title="注册工具">
              <ChipList items={detail.tools ?? []} />
            </Section>
          </>
        )}
      </div>

      {/* 编辑弹窗（自建技能/专家） */}
      {editing !== undefined && detail.kind === 'skill' && (
        <SkillModal
          initial={editing}
          onClose={(changed) => {
            setEditing(undefined)
            if (changed) void refresh()
          }}
        />
      )}
      {editing !== undefined && detail.kind === 'expert' && (
        <ExpertEditor
          initial={editing}
          onClose={(changed) => {
            setEditing(undefined)
            if (changed) void refresh()
          }}
        />
      )}

      {/* 插件安装配置弹窗 */}
      {configuring && (
        <PluginInstallModal
          item={configuring}
          onClose={() => setConfiguring(null)}
          onDone={(message) => {
            setConfiguring(null)
            toast('success', message)
            void refresh()
          }}
        />
      )}

      {/* 插件个人配置编辑弹窗（已安装；保存后刷新详情，让"已配置"标记与预填值同步） */}
      {editingConfig && detail.kind === 'plugin' && (
        <PluginEditConfigModal
          pluginId={detail.id}
          name={detail.name}
          schema={detail.config_schema ?? []}
          current={detail.config ?? {}}
          secretsSet={detail.secrets_set ?? {}}
          readyKeys={detail.config_ready_keys ?? []}
          onClose={() => setEditingConfig(false)}
          onDone={(message) => {
            setEditingConfig(false)
            toast('success', message)
            void refresh()
          }}
        />
      )}
    </div>
  )
}
