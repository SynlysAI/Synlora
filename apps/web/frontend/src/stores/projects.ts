/**
 * 项目（工作区）store：当前用户的项目列表 + 当前选中工作区。
 *
 * 启动时必须调用 load()：后端 GET /api/v1/projects 首次访问会执行旧布局
 * 迁移并补种「默认项目」，会话创建与文件上传都依赖迁移后的项目存在。
 *
 * `currentId` 是**用户显式选择**：`null` = 没选（不是"没有项目"），语义是
 * 「用默认工作区」——新建会话不传 project_id 由后端回落，只读消费侧（上传
 * 目录 / 右栏文件树）用 pickActiveProject 回落默认工作区。故 load() **不**
 * 自动选中列表首个：自动选中会把"没选"这个状态吃掉，用户清掉后又被选回来。
 */
import { create } from 'zustand'
import type { Project } from '@/types'
import { api } from '@/api/client'

/** 默认工作区的磁盘目录名（后端 `workspace.DEFAULT_PROJECT_DIR`）。 */
export const DEFAULT_PROJECT_DIR = 'default'

interface ProjectsState {
  /** 当前用户未归档项目列表（后端排序）。 */
  projects: Project[]
  /** 当前显式选中的工作区 id（null = 未选，消费侧回落默认工作区）。 */
  currentId: string | null
  /** load() 是否完成（空态区分加载中）。 */
  loaded: boolean
  /** 拉取项目列表（触发后端迁移，幂等）。 */
  load: () => Promise<void>
  /** 新建项目并切为当前（重名后端自动加目录后缀，不失败）。 */
  create: (name: string) => Promise<Project>
  /** 重命名项目（后端同步改磁盘目录名；重名自动加后缀）。 */
  rename: (id: string, name: string) => Promise<Project>
  /** 删除项目（记录 + 磁盘目录；其下会话由 Sidebar 归入默认工作区，不级联删除）。 */
  remove: (id: string) => Promise<void>
  /** 切换当前项目。 */
  setCurrent: (id: string | null) => void
  /** 清空全部状态（切换账号时调用，防止上一账号的项目被新账号引用）。 */
  reset: () => void
}

export const useProjectsStore = create<ProjectsState>((set, get) => ({
  projects: [],
  currentId: null,
  loaded: false,

  load: async () => {
    const projects = await api<Project[]>('/api/v1/projects')
    const { currentId } = get()
    // 显式选择若仍有效则保留，否则回到「未选」（不自动选中首个，见文件头）
    const stillValid = currentId !== null && projects.some((p) => p._id === currentId)
    set({ projects, currentId: stillValid ? currentId : null, loaded: true })
  },

  create: async (name) => {
    const trimmed = name.trim()
    // 显示名唯一：本地先挡一道（省一次请求、报错更及时）；后端仍有权威校验（409）
    if (get().projects.some((p) => p.name === trimmed)) {
      throw new Error(`已存在同名工作区「${trimmed}」`)
    }
    const project = await api<Project>('/api/v1/projects', {
      method: 'POST',
      body: { name: trimmed },
    })
    // 新项目置于列表头并选中（后端列表按 updated_at 倒序，头插与刷新后的顺序一致）
    set((s) => ({ projects: [project, ...s.projects], currentId: project._id }))
    return project
  },

  rename: async (id, name) => {
    const trimmed = name.trim()
    // 撞上别人已用的名字本地先挡一道（排除自己，改回原名不算撞）
    if (get().projects.some((p) => p._id !== id && p.name === trimmed)) {
      throw new Error(`已存在同名工作区「${trimmed}」`)
    }
    const project = await api<Project>(`/api/v1/projects/${id}`, {
      method: 'PATCH',
      body: { name: trimmed },
    })
    set((s) => ({
      // 重命名会刷新 updated_at，同步移到列表头（与后端倒序一致，刷新后位置不变）
      projects: [project, ...s.projects.filter((p) => p._id !== id)],
    }))
    return project
  },

  remove: async (id) => {
    await api(`/api/v1/projects/${id}`, { method: 'DELETE' })
    set((s) => ({
      projects: s.projects.filter((p) => p._id !== id),
      // 删掉的正是当前选中 → 回到「未选」，消费侧回落默认工作区
      currentId: s.currentId === id ? null : s.currentId,
    }))
  },

  setCurrent: (id) => set({ currentId: id }),

  reset: () => set({ projects: [], currentId: null, loaded: false }),
}))

/**
 * 取默认工作区（`dir_name === 'default'`，后端补种保证存在）。
 *
 * @param state projects store 快照。
 * @returns 默认工作区；列表未加载时 null。
 */
export function pickDefaultProject(state: ProjectsState): Project | null {
  return state.projects.find((p) => p.dir_name === DEFAULT_PROJECT_DIR) ?? null
}

/**
 * 取当前**生效**的工作区：显式选中优先；未选（currentId 为 null）回落默认
 * 工作区，没有默认工作区再回落列表首个——与"没选就用默认工作区"的语义一致，
 * 供上传落盘目录 / 右栏文件树这类只读消费侧使用（新建会话仍走"不传
 * project_id 由后端回落"的路径，见 sessions store）。
 *
 * @param state projects store 快照。
 * @returns 当前生效的工作区；项目列表为空时 null。
 */
export function pickActiveProject(state: ProjectsState): Project | null {
  return (
    state.projects.find((p) => p._id === state.currentId) ??
    pickDefaultProject(state) ??
    state.projects[0] ??
    null
  )
}
