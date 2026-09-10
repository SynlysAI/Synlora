/**
 * 用户工作区文件 store：右栏 Files 面板与上传/删除后的列表刷新共用。
 */
import { create } from 'zustand'
import type { FileDoc } from '@/types'
import { api } from '@/api/client'

interface FilesState {
  /** 当前用户文件列表（created_at 倒序，后端排序）。 */
  files: FileDoc[]
  /** load() 是否完成（空态区分加载中）。 */
  loaded: boolean
  /** 拉取文件列表。 */
  load: () => Promise<void>
}

export const useFilesStore = create<FilesState>((set) => ({
  files: [],
  loaded: false,

  load: async () => {
    const files = await api<FileDoc[]>('/api/v1/files')
    set({ files, loaded: true })
  },
}))
