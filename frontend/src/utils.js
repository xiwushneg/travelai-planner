export const categoryLabels = {
  culture: '文化', nature: '自然', modern: '现代', shopping: '购物', garden: '庭园', family: '亲子',
}

export const verificationLabels = {
  unverified: '未实时核验',
  verified: '已核验',
  stale: '可能过期',
  unknown: '状态未知',
}

export function formatDate(value, withYear = true) {
  if (!value) return '—'
  const date = new Date(`${value}T12:00:00`)
  return new Intl.DateTimeFormat('zh-CN', {
    ...(withYear ? { year: 'numeric' } : {}), month: 'short', day: 'numeric', weekday: 'short',
  }).format(date)
}

export function formatDateTime(value) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value))
}

export function money(value) {
  return new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY', maximumFractionDigits: 0 }).format(value || 0)
}

export function safeGetTripId() {
  try { return window.localStorage.getItem('travelai:last-trip') } catch { return null }
}

export function safeSetTripId(value) {
  try {
    if (value) window.localStorage.setItem('travelai:last-trip', value)
    else window.localStorage.removeItem('travelai:last-trip')
  } catch { /* 隐私模式下仍可在当前会话使用 */ }
}

export function downloadJson(filename, value) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}
