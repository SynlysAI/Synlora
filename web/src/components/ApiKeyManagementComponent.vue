<template>
  <div class="apikey-management">
    <!-- 头部区域 -->
    <div class="header-section">
      <div class="header-content">
        <div class="section-title">API Key 管理</div>
        <p class="section-description">
          用于外部系统调用 Agent 对话接口。密钥可在创建后随时在列表操作列中复制，请妥善保管。
        </p>
      </div>
      <div class="header-actions">
        <a-button
          @click="handleRefresh"
          :loading="refreshing"
          title="刷新"
          class="refresh-btn lucide-icon-btn"
        >
          <template #icon><RefreshCw :size="16" :class="{ spin: refreshing }" /></template>
        </a-button>
        <a-button type="primary" @click="showCreateModal" class="add-btn lucide-icon-btn">
          <Plus :size="14" />
          创建 API Key
        </a-button>
      </div>
    </div>

    <!-- 主内容区域 -->
    <div class="content-section">
      <a-spin :spinning="loading">
        <div v-if="error" class="error-message">
          <a-alert type="error" :message="error" show-icon />
        </div>

        <template v-if="apiKeys.length > 0">
          <div class="settings-table-wrapper">
            <a-table
              :dataSource="apiKeys"
              :columns="columns"
              :rowKey="(record) => record.id"
              :pagination="false"
              class="settings-table"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'name'">
                  <div class="table-cell-title">
                    <KeyIcon :size="15" class="cell-icon" />
                    <span class="cell-main-text" :title="record.name">{{ record.name }}</span>
                  </div>
                </template>
                <template v-if="column.key === 'prefix'">
                  <code class="code-badge">{{ record.key_prefix }}****</code>
                </template>
                <template v-if="column.key === 'status'">
                  <div class="status-cell">
                    <a-switch
                      :checked="record.is_enabled"
                      size="small"
                      @change="toggleEnabled(record)"
                    />
                  </div>
                </template>
                <template v-if="column.key === 'lastUsed'">
                  <span class="time-text">{{ formatTime(record.last_used_at) }}</span>
                </template>
                <template v-if="column.key === 'expiresAt'">
                  <span class="time-text">{{ record.expires_at || '永不过期' }}</span>
                </template>
                <template v-if="column.key === 'action'">
                  <div class="action-btns">
                    <a-tooltip title="复制密钥">
                      <a-button
                        type="text"
                        size="small"
                        class="action-btn lucide-icon-btn"
                        :loading="revealLoadingId === record.id"
                        @click="copyKeySecret(record)"
                      >
                        <Copy :size="14" />
                      </a-button>
                    </a-tooltip>
                    <a-popconfirm
                      title="确定要删除此 API Key 吗？此操作不可恢复。"
                      @confirm="deleteKey(record)"
                      ok-text="确定"
                      cancel-text="取消"
                    >
                      <a-tooltip title="删除 API Key">
                        <a-button type="text" size="small" danger class="action-btn lucide-icon-btn">
                          <Trash2 :size="14" />
                        </a-button>
                      </a-tooltip>
                    </a-popconfirm>
                  </div>
                </template>
              </template>
            </a-table>
          </div>
        </template>

        <div v-else class="empty-state">
          <a-empty description="暂无 API Key，点击上方按钮创建一个" />
        </div>
      </a-spin>
    </div>

    <!-- 创建 Modal -->
    <a-modal
      v-model:open="createModalVisible"
      title="创建 API Key"
      @ok="handleCreate"
      @cancel="handleCreateCancel"
      :confirmLoading="createLoading"
      ok-text="创建"
      cancel-text="取消"
    >
      <a-form layout="vertical" :model="createForm">
        <a-form-item label="名称" required>
          <a-input v-model:value="createForm.name" placeholder="如：生产环境API" />
        </a-form-item>
        <a-form-item label="过期时间">
          <a-date-picker
            v-model:value="createForm.expires_at"
            show-time
            placeholder="留空表示永不过期"
            style="width: 100%"
          />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 密钥显示 Modal (创建后展示，列表中也可随时复制) -->
    <a-modal
      v-model:open="secretModalVisible"
      title="API Key 已创建"
      :closable="true"
      @cancel="secretModalVisible = false"
      :footer="null"
      width="520px"
    >
      <div class="secret-display">
        <a-alert
          type="warning"
          message="请妥善保管密钥，也可以稍后在列表操作列中复制"
          show-icon
          class="secret-alert"
        />
        <div class="secret-value-container">
          <code class="secret-value">{{ createdSecret }}</code>
          <a-button type="primary" @click="copySecret" class="copy-btn lucide-icon-btn">
            <Copy :size="14" />
            复制
          </a-button>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { message } from 'ant-design-vue'
import { Plus, RefreshCw, Trash2, Copy } from '@lucide/vue'
import { Key as KeyIcon } from '@lucide/vue'
import { apikeyApi } from '@/apis/apikey_api'
import { copyTextToClipboard } from '@/utils/clipboard'

const loading = ref(false)
const refreshing = ref(false)
const error = ref(null)
const apiKeys = ref([])

const createModalVisible = ref(false)
const secretModalVisible = ref(false)
const createLoading = ref(false)
const createdSecret = ref('')
const createRequestId = ref('')
const CREATE_REQUEST_STORAGE_KEY = 'yuxi_pending_api_key_request_id'

const columns = [
  { title: '名称', dataIndex: 'name', key: 'name', width: '22%' },
  { title: '前缀', dataIndex: 'key_prefix', key: 'prefix', width: '18%' },
  { title: '状态', dataIndex: 'is_enabled', key: 'status', width: '14%' },
  { title: '最后使用', dataIndex: 'last_used_at', key: 'lastUsed', width: '18%' },
  { title: '过期时间', dataIndex: 'expires_at', key: 'expiresAt', width: '18%' },
  { title: '操作', key: 'action', width: 90, align: 'center' }
]

const createForm = reactive({
  name: '',
  expires_at: null
})

const formatTime = (timeStr) => {
  if (!timeStr) return '-'
  const date = new Date(timeStr)
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

const loadApiKeys = async () => {
  loading.value = true
  error.value = null
  try {
    const res = await apikeyApi.list()
    apiKeys.value = res.api_keys || []
  } catch (e) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

// 刷新 API Key 列表
const handleRefresh = async () => {
  if (refreshing.value) return
  refreshing.value = true
  try {
    await loadApiKeys()
    message.success('刷新成功')
  } catch (e) {
    console.error('刷新失败:', e)
    message.error('刷新失败')
  } finally {
    refreshing.value = false
  }
}

const showCreateModal = () => {
  createForm.name = ''
  createForm.expires_at = null
  createRequestId.value =
    sessionStorage.getItem(CREATE_REQUEST_STORAGE_KEY) ||
    Array.from(globalThis.crypto.getRandomValues(new Uint8Array(16)), (byte) =>
      byte.toString(16).padStart(2, '0')
    ).join('')
  sessionStorage.setItem(CREATE_REQUEST_STORAGE_KEY, createRequestId.value)
  createModalVisible.value = true
}

const handleCreateCancel = () => {
  sessionStorage.removeItem(CREATE_REQUEST_STORAGE_KEY)
  createRequestId.value = ''
}

const handleCreate = async () => {
  if (!createForm.name.trim()) {
    message.error('请输入名称')
    return
  }

  createLoading.value = true
  try {
    const data = { name: createForm.name, request_id: createRequestId.value }
    if (createForm.expires_at) {
      data.expires_at = createForm.expires_at.format('YYYY-MM-DDTHH:mm:ss')
    }

    const res = await apikeyApi.create(data)
    sessionStorage.removeItem(CREATE_REQUEST_STORAGE_KEY)
    createRequestId.value = ''
    createdSecret.value = res.secret
    createModalVisible.value = false
    secretModalVisible.value = true
    await loadApiKeys()
  } catch (e) {
    message.error(e.message || '创建失败')
  } finally {
    createLoading.value = false
  }
}

const copySecret = async () => {
  try {
    await copyTextToClipboard(createdSecret.value)
    message.success('已复制到剪贴板')
  } catch {
    message.error('复制失败')
  }
}

// 列表行复制：服务端重新派生完整密钥后写入剪贴板
const revealLoadingId = ref(null)
const copyKeySecret = async (key) => {
  revealLoadingId.value = key.id
  try {
    const res = await apikeyApi.revealSecret(key.id)
    await copyTextToClipboard(res.secret)
    message.success('已复制到剪贴板')
  } catch (e) {
    // 409 = CLI 授权等派生域密钥无法重建，base.js 会把它泛化成"请刷新重试"，这里给出准确提示
    message.error(e.status === 409 ? '该密钥不支持在此查看，请在创建它的客户端中获取' : e.message || '复制失败')
  } finally {
    revealLoadingId.value = null
  }
}

const toggleEnabled = async (key) => {
  try {
    await apikeyApi.update(key.id, { is_enabled: !key.is_enabled })
    message.success(key.is_enabled ? '已禁用' : '已启用')
    await loadApiKeys()
  } catch (e) {
    message.error(e.message || '操作失败')
  }
}

const deleteKey = async (key) => {
  try {
    await apikeyApi.delete(key.id)
    message.success('删除成功')
    await loadApiKeys()
  } catch (e) {
    message.error(e.message || '删除失败')
  }
}

onMounted(() => {
  loadApiKeys()
})
</script>

<style lang="less" scoped>
.apikey-management {
  .header-section {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 16px;
    margin-bottom: 16px;

    .header-content {
      flex: 1;
      min-width: 0;

      .section-title {
        font-size: 16px;
        font-weight: 500;
        color: var(--gray-900);
        line-height: 1.4;
        margin: 12px 0 12px;
      }

      .section-description {
        font-size: 14px;
        color: var(--gray-600);
        line-height: 1.4;
        margin: 0;
      }
    }

    .header-actions {
      display: flex;
      align-items: center;
      gap: 8px;

      .refresh-btn {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 32px;
        height: 32px;
        border-radius: 6px;
        transition: all 0.2s ease;

        &:hover {
          background: var(--gray-25);
        }

        .spin {
          animation: spin 1s linear infinite;
        }
      }
    }
  }

  .content-section {
    .error-message {
      margin-bottom: 16px;
    }

    .empty-state {
      padding: 48px 0;
    }

    .settings-table-wrapper {
      border: 1px solid var(--gray-150);
      border-radius: 8px;
      overflow: hidden;
      background: var(--gray-0);

      :deep(.ant-table) {
        background: transparent;
        font-size: 13px;
      }

      :deep(.ant-table-thead > tr > th) {
        background: var(--gray-50);
        color: var(--gray-500);
        font-weight: 500;
        font-size: 12px;
        padding: 9px 14px;
        border-bottom: 1px solid var(--gray-150);
        white-space: nowrap;

        &::before {
          display: none !important;
        }
      }

      :deep(.ant-table-tbody > tr > td) {
        padding: 10px 14px;
        color: var(--gray-800);
        border-bottom: 1px solid var(--gray-100);
        transition: background 0.15s ease;
      }

      :deep(.ant-table-tbody > tr:last-child > td) {
        border-bottom: none;
      }

      :deep(.ant-table-tbody > tr:hover > td) {
        background: var(--gray-25) !important;
      }

      .table-cell-title {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        min-width: 0;
        max-width: 100%;

        .cell-icon {
          color: var(--gray-400);
          flex-shrink: 0;
        }

        .cell-main-text {
          font-weight: 500;
          color: var(--gray-900);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
      }

      .code-badge {
        font-family: 'JetBrains Mono', 'Fira Code', 'Menlo', monospace;
        font-size: 12px;
        color: var(--gray-700);
        background: var(--gray-50);
        border: 1px solid var(--gray-200);
        border-radius: 4px;
        padding: 2px 6px;
        letter-spacing: 0.5px;
      }

      .status-cell {
        display: inline-flex;
        align-items: center;
        gap: 8px;

        :deep(.ant-switch.ant-switch-checked) {
          background-color: var(--gray-700) !important;

          &:hover:not(.ant-switch-disabled) {
            background-color: var(--gray-800) !important;
          }
        }

        .status-text {
          font-size: 12px;
          color: var(--gray-400);

          &.enabled {
            color: var(--gray-700);
            font-weight: 500;
          }
        }
      }

      .time-text {
        color: var(--gray-500);
        font-size: 12px;
      }

      .action-btns {
        display: inline-flex;
        align-items: center;
        gap: 4px;
      }

      .action-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 26px;
        height: 26px;
        border-radius: 6px;
        color: var(--gray-400);
        transition: all 0.15s ease;

        &:hover:not(:disabled) {
          background: var(--gray-100);
          color: var(--gray-800);
        }

        &.ant-btn-dangerous:hover:not(:disabled) {
          background: var(--color-error-50, #fff2f0);
          color: var(--color-error-500, #ff4d4f);
        }
      }
    }
  }
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.secret-display {
  .secret-alert {
    margin-bottom: 16px;
  }

  .secret-value-container {
    display: flex;
    gap: 8px;
    align-items: stretch;

    .secret-value {
      flex: 1;
      font-family: 'Monaco', 'Consolas', monospace;
      font-size: 13px;
      background: var(--gray-100);
      border: 1px solid var(--gray-200);
      border-radius: 6px;
      padding: 12px;
      word-break: break-all;
      color: var(--gray-900);
    }

    .copy-btn {
      flex-shrink: 0;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
  }
}
</style>
