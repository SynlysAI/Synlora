/**
 * 剪贴板写入（含非安全上下文兜底）。
 *
 * `navigator.clipboard` 只存在于安全上下文（HTTPS / localhost / 127.0.0.1）。
 * 外部用户经 `http://<局域网 IP>:8005` 访问时它是 undefined，直接调用会静默
 * 失败（表现为「点复制没反应」）——故回落 textarea + `document.execCommand`。
 */

/**
 * 把文本写入剪贴板。
 *
 * @param text 待复制文本。
 * @returns 是否复制成功（调用方据此决定是否给出「已复制」反馈）。
 */
export async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // 权限被拒 / 非聚焦文档等：继续走兜底路径
    }
  }
  return legacyCopy(text)
}

/**
 * 兜底复制：临时 textarea 选中后走 `document.execCommand('copy')`。
 *
 * execCommand 已废弃，但浏览器仍普遍支持，且是唯一不依赖安全上下文的方案；
 * textarea 不能用 `display:none` / `visibility:hidden`（选区会失效），
 * 用透明 + 固定定位藏在视口内。
 *
 * @param text 待复制文本。
 * @returns 是否复制成功。
 */
function legacyCopy(text: string): boolean {
  const ta = document.createElement('textarea')
  ta.value = text
  ta.setAttribute('readonly', '')
  ta.style.position = 'fixed'
  ta.style.top = '0'
  ta.style.left = '0'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.select()
  ta.setSelectionRange(0, ta.value.length)
  let ok = false
  try {
    ok = document.execCommand('copy')
  } catch {
    ok = false
  }
  ta.remove()
  return ok
}
