/**
 * 技能 store（用户侧轻量视图）：供对话输入区「+」菜单的技能选择面板使用
 * （管理页完整 CRUD 走 admin store 的同一接口）。
 *
 * 与 models store 同形：只做一次列表拉取 + loaded 标记，选择态由调用方
 * （Composer）本地持有，不进 store——技能是「本轮一次性」语义，发送后清空。
 */
import { create } from 'zustand'
import type { Skill } from '@/types'
import { api } from '@/api/client'

interface SkillsState {
  /** 全部技能（含内置）。 */
  skills: Skill[]
  /** load() 是否完成（失败保持 false，面板显示空态文案）。 */
  loaded: boolean
  /** 拉取技能列表（幂等，失败由调用方空态兜底）。 */
  load: () => Promise<void>
}

export const useSkillsStore = create<SkillsState>((set) => ({
  skills: [],
  loaded: false,

  load: async () => {
    // scope=usable：对话可用集（内置默认启用 + 已安装启用 + 自建/公共层），
    // 不分角色——管理员在对话面板也只看到可用技能，全量口径留给管理页
    const skills = await api<Skill[]>('/api/v1/skills?scope=usable')
    set({ skills, loaded: true })
  },
}))
