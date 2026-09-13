/**
 * 统一 API 客户端：token 注入、JSON/form 序列化、错误归一为 ApiError。
 *
 * 401 时清除本地 token 并派发 "sa:unauthorized" 事件
 * （auth store 监听该事件执行登出，回到登录页）。
 */

/** token 在 localStorage 的持久化键。 */
export const TOKEN_STORAGE_KEY = 'sa.token'

/** 401 失效事件名（auth store 监听）。 */
export const UNAUTHORIZED_EVENT = 'sa:unauthorized'

/** API 错误：非 2xx 响应统一抛出（detail 为后端 {detail} 原始值）。 */
export class ApiError extends Error {
  /** HTTP 状态码。 */
  status: number
  /** 后端错误详情：string / FastAPI 422 校验数组 / undefined。 */
  detail?: unknown

  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

/** 读取当前 token（null 表示未登录）。 */
export function getToken(): string | null {
  return localStorage.getItem(TOKEN_STORAGE_KEY)
}

/** 写入/清除 token。 */
export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_STORAGE_KEY, token)
  else localStorage.removeItem(TOKEN_STORAGE_KEY)
}

/** 从错误响应体提取 detail 并归一为 ApiError。 */
async function toApiError(resp: Response): Promise<ApiError> {
  let detail: unknown
  try {
    const body = await resp.json()
    if (body && typeof body === 'object' && 'detail' in body) detail = body.detail
    else detail = body
  } catch {
    // 响应体非 JSON（如网关错误页）时 detail 留空
  }
  const message =
    typeof detail === 'string'
      ? detail
      : `请求失败（HTTP ${resp.status}）`
  return new ApiError(resp.status, message, detail)
}

interface ApiOptions {
  /** HTTP 方法，默认 GET。 */
  method?: string
  /** JSON 请求体（自动序列化并设置 Content-Type）。 */
  body?: unknown
  /** multipart 表单（直接传递，浏览器自动设置边界）。 */
  form?: FormData
}

/**
 * 发起 API 请求并解析 JSON 响应。
 *
 * @param path 请求路径（如 /api/v1/auth/login）。
 * @param opts 请求选项：method / body / form。
 * @returns 解析后的 JSON 响应（T 由调用方标注）。
 * @throws ApiError 非 2xx 响应；401 同时清 token 并派发 sa:unauthorized。
 */
export async function api<T>(path: string, opts: ApiOptions = {}): Promise<T> {
  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  let body: BodyInit | undefined
  if (opts.form) {
    body = opts.form
  } else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(opts.body)
  }

  const resp = await fetch(path, {
    method: opts.method ?? 'GET',
    headers,
    body,
  })

  if (!resp.ok) {
    if (resp.status === 401) {
      setToken(null)
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    }
    throw await toApiError(resp)
  }

  // 204 / 空响应体：返回 undefined（调用方一般约定 2xx 均有 JSON，此处兜底）
  if (resp.status === 204) return undefined as T
  const text = await resp.text()
  return (text ? JSON.parse(text) : undefined) as T
}

/**
 * 带鉴权下载文件并触发浏览器保存。
 *
 * 裸 `<a href>` 跳转不带 Authorization 头，鉴权端点会回 401 的 JSON 错误体并被
 * 浏览器存成 download.json——必须用 fetch（注入 token）取 blob 后程序化下载。
 *
 * @param path 下载端点路径（如 /api/v1/files/{id}/download）。
 * @param fallbackName 响应未带 Content-Disposition 文件名时的兜底文件名。
 * @throws ApiError 非 2xx 响应；401 同时清 token 并派发 sa:unauthorized。
 */
export async function downloadFile(path: string, fallbackName: string): Promise<void> {
  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  const resp = await fetch(path, { headers })
  if (!resp.ok) {
    if (resp.status === 401) {
      setToken(null)
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    }
    throw await toApiError(resp)
  }
  const blob = await resp.blob()
  // 文件名优先取 Content-Disposition（RFC 5987 filename* 优先，兼容 filename）
  let filename = fallbackName
  const disposition = resp.headers.get('content-disposition') ?? ''
  const star = disposition.match(/filename\*=(?:UTF-8'')?([^;]+)/i)
  const plain = disposition.match(/filename="?([^";]+)"?/i)
  try {
    if (star?.[1]) filename = decodeURIComponent(star[1].trim())
    else if (plain?.[1]) filename = plain[1].trim()
  } catch {
    // 文件名解析失败保持兜底名
  }
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
