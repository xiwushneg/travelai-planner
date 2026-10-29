const API_ROOT = '/api/v1'

export class ApiError extends Error {
  constructor(message, status, payload) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.payload = payload
    this.detail = payload?.detail
  }
}

// 统一 401 处理：会话过期或已登出时由 App 切回登录页。
let unauthorizedHandler = null

export function setUnauthorizedHandler(handler) {
  unauthorizedHandler = handler
}

async function request(path, options = {}) {
  let response
  try {
    response = await fetch(`${API_ROOT}${path}`, {
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', ...options.headers },
      ...options,
    })
  } catch (error) {
    throw new ApiError('无法连接规划服务，请确认后端已启动。', 0, null)
  }
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = payload?.detail
    const message = typeof detail === 'string'
      ? detail
      : detail?.message || detail?.conflicts?.[0]?.message || `请求失败（${response.status}）`
    // 登录/注册/登出自身的 401 由调用方处理，避免误报“登录状态已失效”。
    const authEndpoint = path.startsWith('/auth/')
    if (response.status === 401 && unauthorizedHandler && !authEndpoint) unauthorizedHandler()
    throw new ApiError(message, response.status, payload)
  }
  return payload
}

export const api = {
  health: () => fetch('/health').then((response) => {
    if (!response.ok) throw new Error('服务不可用')
    return response.json()
  }),
  me: () => request('/auth/me'),
  register: (username, password) => request('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  }),
  login: (username, password) => request('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  }),
  logout: () => request('/auth/logout', { method: 'POST' }),
  logoutAll: () => request('/auth/logout-all', { method: 'POST' }),
  listSpots: (city) => request(`/spots${city ? `?city=${encodeURIComponent(city)}` : ''}`),
  listTrips: () => request('/trips'),
  createTrip: (spec, includeEvidence = true) => request('/trips', {
    method: 'POST',
    body: JSON.stringify({ spec, include_evidence: includeEvidence }),
  }),
  getTrip: (tripId, version) => request(`/trips/${tripId}${version ? `?version=${version}` : ''}`),
  listVersions: (tripId) => request(`/trips/${tripId}/versions`),
  compare: (tripId, fromVersion, toVersion) => request(
    `/trips/${tripId}/diff?from_version=${fromVersion}&to_version=${toVersion}`,
  ),
  modifyDay: (tripId, body) => request(`/trips/${tripId}/modify-day`, {
    method: 'POST',
    body: JSON.stringify(body),
  }),
}
