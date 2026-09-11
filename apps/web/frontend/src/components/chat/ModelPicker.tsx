/**
 * 会话级模型选择器（输入框左下角小下拉）：
 *
 * - 当前模型名按钮 + 下拉：首项"跟随助手（xxx）"恢复助手绑定默认，
 *   其余为管理员配置的 enabled 模型，选择即 PATCH 当前会话的
 *   model_provider_id（后端对话时按 会话级覆盖 > 助手绑定 取模型）；
 * - **无专家时不能停在"跟随助手"**：专家是可选后，没有助手就没有可回落的
 *   模型服务，后端 `send_message` 会 422「未指定模型服务」。此时下拉不再提供
 *   "跟随助手"项，并**自动把第一个 enabled 模型落到会话级 model_provider_id**
 *   （见下方 effect），显示与实际下发都是这个显式模型；
 * - **草稿态（会话还没落库）也可用**：懒创建下会话首次发送才建，此时没有会话可
 *   PATCH，选择先存 sessions store 的 draftModelProviderId，建会话时随请求下发；
 * - 模型列表空（管理员未配置）时隐藏下拉、显示
 *   "未配置模型"小字；
 * - 会话切换时随 sessions store 的 model_provider_id 自动联动显示。
 */
import { useEffect, useRef, useState } from 'react'
import { pickSelectedAssistant, useAssistantsStore } from '@/stores/assistants'
import { useModelsStore } from '@/stores/models'
import { useSessionsStore } from '@/stores/sessions'
import { toast } from '@/stores/toasts'

/** 会话级模型选择器组件（Composer 内输入框下方左侧）。 */
export default function ModelPicker() {
  const currentId = useSessionsStore((s) => s.currentId)
  const session = useSessionsStore((s) =>
    s.sessions.find((x) => x._id === s.currentId),
  )
  const draftModelId = useSessionsStore((s) => s.draftModelProviderId)
  const setDraftModel = useSessionsStore((s) => s.setDraftModel)
  const setModel = useSessionsStore((s) => s.setModel)
  const models = useModelsStore((s) => s.models)
  const modelsLoaded = useModelsStore((s) => s.loaded)
  const assistantsLoaded = useAssistantsStore((s) => s.loaded)
  const defaultAssistant = useAssistantsStore(pickSelectedAssistant)
  const assistant = useAssistantsStore((s) =>
    s.assistants.find((a) => a._id === session?.assistant_id),
  )
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  // 懒加载 enabled 模型列表（失败静默，空态文案兜底）
  useEffect(() => {
    void useModelsStore.getState().load().catch(() => {})
  }, [])

  // 点击组件外部关闭下拉
  useEffect(() => {
    if (!open) return
    const onDocClick = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [open])

  // 草稿态（会话尚未落库）：没有会话可 PATCH，选择先存 store，建会话时下发。
  // 此时「当前专家」取新对话默认专家——它决定新建会话是否绑定专家。
  const drafting = !currentId
  const boundAssistant = drafting ? defaultAssistant : assistant
  const boundAssistantId = drafting
    ? (defaultAssistant?._id ?? null)
    : (session?.assistant_id ?? null)

  // 无专家（未绑定专家，或会话绑定的专家已不存在）→ 必须显式指定模型
  const hasAssistantBinding = Boolean(boundAssistantId)
  const needsExplicitModel =
    !hasAssistantBinding || (!drafting && assistantsLoaded && !assistant)
  const overrideId = drafting ? draftModelId : (session?.model_provider_id ?? null)
  // 无专家时的有效模型：已有选择仍可用则沿用，否则回落第一个 enabled 模型
  const explicitId = needsExplicitModel
    ? overrideId && models.some((m) => m._id === overrideId)
      ? overrideId
      : (models[0]?._id ?? null)
    : overrideId

  // 自动下发：无专家且还没有可用的模型时——草稿态写进 draft，有会话则 PATCH
  useEffect(() => {
    if (!needsExplicitModel || !modelsLoaded || !explicitId || explicitId === overrideId) return
    if (drafting) {
      setDraftModel(explicitId)
      return
    }
    if (!currentId) return
    void setModel(currentId, explicitId).catch(() => {
      // 失败静默：按钮仍显示该模型，用户可手动重选（错误由下拉操作路径提示）
    })
  }, [needsExplicitModel, drafting, currentId, modelsLoaded, explicitId, overrideId,
      setModel, setDraftModel])

  /** 跟随助手时的助手侧模型名（联查字段：正常名 / "(已停用)" / "(已删除)" / null=未绑定）。 */
  const assistantModel = boundAssistant?.model_name
  const followLabel = `跟随助手（${assistantModel ?? '未绑定模型'}）`
  /** 当前生效的模型 id：无专家走显式模型，有专家走会话覆盖（null = 跟随助手）。 */
  const effectiveId = needsExplicitModel ? explicitId : overrideId
  const effectiveModel = models.find((m) => m._id === effectiveId)
  const currentLabel = needsExplicitModel
    ? (effectiveModel?.name ?? (modelsLoaded ? '（已停用或已删除）' : '加载中…'))
    : overrideId
      ? (effectiveModel?.name ?? '（已停用或已删除）')
      : followLabel

  /** 选中某项：草稿态写入 draft，有会话则 PATCH 会话覆盖字段。 */
  const pick = (id: string | null) => {
    setOpen(false)
    if (drafting) {
      setDraftModel(id)
      return
    }
    void setModel(currentId!, id).catch((err) => {
      toast('error', `切换模型失败：${(err as Error).message}`)
    })
  }

  const itemClass = (active: boolean) =>
    'flex w-full items-center gap-2 rounded-[var(--sa-radius-sm)] px-2.5 py-1.5 text-left text-[13px] transition-colors duration-[var(--sa-duration-fast)] ' +
    (active
      ? 'bg-[var(--sa-alias-interactive-bg-hover)] font-medium text-[var(--sa-alias-label-primary)]'
      : 'text-[var(--sa-alias-label-secondary)] hover:bg-[var(--sa-alias-interactive-bg-hover)] hover:text-[var(--sa-alias-label-primary)]')

  return (
    <div ref={rootRef} className="relative">
      {modelsLoaded && models.length === 0 ? (
        <span className="px-0.5 text-xs text-[var(--sa-alias-label-caption)]">
          未配置模型
        </span>
      ) : (
        <>
          <button
            type="button"
            aria-haspopup="menu"
            aria-expanded={open}
            aria-label={`会话模型：${currentLabel}，点击切换`}
            title="切换本会话使用的模型"
            onClick={() => setOpen((v) => !v)}
            className="flex items-center gap-1.5 rounded-[var(--sa-radius-sm)] px-1.5 py-1 text-xs text-[var(--sa-alias-label-secondary)] transition-colors duration-[var(--sa-duration-fast)] hover:bg-[var(--sa-alias-interactive-bg-hover)] hover:text-[var(--sa-alias-label-primary)]"
          >
            <svg
              width="13"
              height="13"
              viewBox="0 0 16 16"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <rect x="1.75" y="2.5" width="12.5" height="8.5" rx="1.5" />
              <path d="M5.5 13.5h5M8 11v2.5" />
            </svg>
            <span className="max-w-[220px] truncate">{currentLabel}</span>
            <svg
              width="10"
              height="10"
              viewBox="0 0 12 12"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
              className={`transition-transform duration-[var(--sa-duration-fast)] ${open ? 'rotate-180' : ''}`}
            >
              <path d="M2.5 4.5 6 8l3.5-3.5" />
            </svg>
          </button>
          {open && (
            <div
              role="menu"
              aria-label="选择会话模型"
              className="absolute bottom-full left-0 z-50 mb-1.5 max-h-64 w-56 overflow-y-auto rounded-[var(--sa-radius-md)] border border-[var(--sa-alias-border-l2)] bg-[var(--sa-alias-bg-layer-1)] p-1 shadow-lg"
            >
              {/* 无专家时说明为何没有"跟随助手"项 */}
              {needsExplicitModel && models.length > 0 && (
                <div className="px-2.5 pb-1 pt-0.5 text-[11px] leading-4 text-[var(--sa-alias-label-caption)]">
                  未使用专家，需指定模型
                </div>
              )}
              {/* 无专家时没有可回落的助手绑定，"跟随助手"项无意义故不渲染 */}
              {!needsExplicitModel && (
                <button
                  role="menuitem"
                  type="button"
                  onClick={() => pick(null)}
                  className={itemClass(!overrideId)}
                >
                  <span className="truncate">{followLabel}</span>
                </button>
              )}
              {models.map((m) => (
                <button
                  key={m._id}
                  role="menuitem"
                  type="button"
                  onClick={() => pick(m._id)}
                  className={itemClass(effectiveId === m._id)}
                >
                  <span className="truncate">{m.name}</span>
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
