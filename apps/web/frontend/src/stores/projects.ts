/**
 * 项目 store：当前用户的项目列表 + 当前选中项目。
 *
 * 启动时必须调用 load()：后端 GET /api/v1/projects 首次访问会执行旧布局
 * 迁移并补种「默认项目」，会话创建与文件上传都依赖迁移后的项目存在。
 * 若 currentId 已失效（被删 / 换了账号）或为空，回落取列表首个项目。
 */
import { create } from 'zustand'
import type { Project } from '@/types'
import { api } from '@/api/client'

interface ProjectsState {
  /** 当前用户未归档项目列表（后端排序）。 */
  projects: Project[]
  /** 当前选中的项目 id（null 表示尚无项目）。 */
  currentId: string | null
  /** load() 是否完成（空态区分加载中）。 */
  loaded: boolean
  /** 拉取项目列表（触发后端迁移，幂等）。 */
  load: () => Promise<void>
  /** 新建项目并切为当前（重名后端自动加目录后缀，不失败）。 */
  create: (name: string) => Promise<Project>
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
    const stillValid = currentId !== null && projects.some((p) => p._id === currentId)
    const next = stillValid ? currentId : (projects[0]?._id ?? null)
    set({ projects, currentId: next, loaded: true })
  },

  create: async (name) => {
    const project = await api<Project>('/api/v1/projects', {
      method: 'POST',
      body: { name },
    })
    // 新项目置于列表尾并选中（后端按创建序返回，插入位置与之保持一致）
    set((s) => ({ projects: [...s.projects, project], currentId: project._id }))
    return project
  },

  setCurrent: (id) => set({ currentId: id }),

  reset: () => set({ projects: [], currentId: null, loaded: false }),
}))
