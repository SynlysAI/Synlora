/**
 * SSE 流式解析：POST fetch + ReadableStream 手工分帧。
 *
 * 后端契约（sessions_api.send_message）：event=事件类型、data=事件 JSON、id=seq。
 * 断连不 cancel：运行由后端执行完落盘，客户端经 events?after_seq=N 补齐。
 */
import { ApiError, getToken } from './client'

/** 解析出的一条 SSE 消息（data 的 JSON.parse 由调用方做）。 */
export interface SseMessage {
  event: string
  id: string
  data: string
}

/**
 * 发起 SSE POST 流并逐帧回调。
 *
 * @param path 请求路径（如 /api/v1/sessions/{sid}/messages）。
 * @param body JSON 请求体。
 * @param onMessage 每条完整消息的回调。
 * @param signal 中止信号（用户取消）。
 * @returns 流正常结束（服务端关闭）时 resolve。
 * @throws ApiError 非 2xx 响应；网络错误原样抛出；AbortError 静默返回。
 */
export async function streamSse(
  path: string,
  body: unknown,
  onMessage: (m: SseMessage) => void,
  signal?: AbortSignal,
): Promise<void> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  let resp: Response
  try {
    resp = await fetch(path, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
      signal,
    })
  } catch (err) {
    // 用户主动中止：静默结束，不上抛
    if (err instanceof DOMException && err.name === 'AbortError') return
    throw err
  }

  // 非 2xx：与 client.ts 相同的 {detail} 解析（401 清 token 逻辑由页面层统一处理）
  if (!resp.ok) {
    let detail: unknown
    try {
      detail = (await resp.json())?.detail
    } catch {
      // 非 JSON 响应体
    }
    throw new ApiError(
      resp.status,
      typeof detail === 'string' ? detail : `请求失败（HTTP ${resp.status}）`,
      detail,
    )
  }

  const reader = resp.body?.getReader()
  if (!reader) throw new Error('当前环境不支持流式响应')

  const decoder = new TextDecoder()
  let buffer = ''

  /** 解析一帧文本为 SseMessage（不匹配任何字段时返回 null）。 */
  const parseFrame = (frame: string): SseMessage | null => {
    let event = 'message'
    let id = ''
    const dataLines: string[] = []
    for (const rawLine of frame.split('\n')) {
      const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('id:')) id = line.slice(3).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
      // 注释行（:开头）与空行忽略
    }
    if (dataLines.length === 0 && !id) return null
    return { event, id, data: dataLines.join('\n') }
  }

  // SSE 以空行分帧（兼容 \n\n 与 \r\n\r\n）
  const frameSeparator = /\r?\n\r?\n/
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let match: RegExpExecArray | null
      while ((match = frameSeparator.exec(buffer))) {
        const frame = buffer.slice(0, match.index)
        buffer = buffer.slice(match.index + match[0].length)
        const msg = parseFrame(frame)
        if (msg) onMessage(msg)
      }
    }
    // 流结束：刷掉残留缓冲中的最后半帧
    buffer += decoder.decode()
    const msg = parseFrame(buffer)
    if (msg) onMessage(msg)
  } catch (err) {
    // 用户主动中止：静默结束
    if (err instanceof DOMException && err.name === 'AbortError') return
    throw err
  }
}
