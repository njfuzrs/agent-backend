/** API 客户端 */

import axios from 'axios'
import type {
  HistoryEntry,
  CostStatsResponse,
  DistributionResponse,
  StatsOverviewResponse,
  TrajectoryBatchUpdateResponse,
  TrajectoryListResponse,
  TrajectoryMeta,
  TrajectoryStepsResponse,
  TrendsResponse,
} from '../types/trajectory'

// 开发环境通过 vite proxy 转发（/api），生产环境走 /traj/api
const isProd = import.meta.env.PROD
export const apiBasePath = isProd ? '/traj/api/v1' : '/api/v1'
// withCredentials：让浏览器带上 HttpOnly 会话 cookie。
// 凭据**不再进 localStorage**（规划 §PR-0.5 第 4 条）——
// 原实现把 Basic Auth 密码明文存在 localStorage，一个 XSS 即可读走管理员凭据。
// 现在 token 在 HttpOnly cookie 里，JS 读不到。
const api = axios.create({
  baseURL: apiBasePath,
  withCredentials: true,
})

const TRAJECTORY_STEPS_BATCH_SIZE = 200

function redirectToLogin() {
  const basename = '/traj'
  const fullPath = window.location.pathname
  const appPath = fullPath.startsWith(basename)
    ? fullPath.slice(basename.length) || '/'
    : fullPath
  if (appPath.startsWith('/login')) return
  const from = encodeURIComponent(appPath + window.location.search)
  window.location.assign(`${basename}/login?from=${from}`)
}

api.interceptors.response.use(
  response => response,
  error => {
    const status = error.response?.status
    const url = String(error.config?.url || '')
    const isAuthEndpoint = url.includes('/auth/login') || url.includes('/auth/me')
    if (status === 401 && !isAuthEndpoint) {
      redirectToLogin()
    }
    return Promise.reject(error)
  }
)

/** 登录：服务端校验后下发 HttpOnly cookie。口令只在本次请求体里出现，不落盘。 */
export async function login(username: string, password: string): Promise<void> {
  await api.post('/auth/login', { username, password })
}

export async function logout(): Promise<void> {
  await api.post('/auth/logout')
}

/** 是否已登录 —— 问服务端，不再读 localStorage。 */
export async function checkAuth(): Promise<boolean> {
  try {
    await api.get('/auth/me')
    return true
  } catch {
    return false
  }
}

/** 轨迹列表 */
export async function fetchTrajectories(params: Record<string, unknown>): Promise<TrajectoryListResponse> {
  const { data } = await api.get('/trajectories', { params })
  return data
}

/** 轨迹元数据 */
export async function fetchTrajectoryMeta(sessionId: string): Promise<TrajectoryMeta> {
  const { data } = await api.get(`/trajectories/${sessionId}`)
  return data
}

/** 轨迹步骤（分页） */
export async function fetchTrajectorySteps(
  sessionId: string,
  offset = 0,
  limit = 50
): Promise<TrajectoryStepsResponse> {
  const { data } = await api.get(`/trajectories/${sessionId}/detail/trajectory`, {
    params: { offset, limit },
  })
  return data
}

/** 轨迹步骤（自动分批拉取全部，避免单次请求过大） */
export async function fetchAllTrajectorySteps(sessionId: string): Promise<TrajectoryStepsResponse> {
  const firstPage = await fetchTrajectorySteps(sessionId, 0, TRAJECTORY_STEPS_BATCH_SIZE)
  if (firstPage.total <= firstPage.items.length) {
    return firstPage
  }

  const requests: Array<Promise<TrajectoryStepsResponse>> = []
  for (let offset = firstPage.items.length; offset < firstPage.total; offset += TRAJECTORY_STEPS_BATCH_SIZE) {
    requests.push(
      fetchTrajectorySteps(
        sessionId,
        offset,
        Math.min(TRAJECTORY_STEPS_BATCH_SIZE, firstPage.total - offset)
      )
    )
  }

  const restPages = await Promise.all(requests)
  return {
    total: firstPage.total,
    offset: 0,
    limit: firstPage.total,
    items: [
      ...firstPage.items,
      ...restPages.flatMap(page => page.items),
    ],
  }
}

/** 轨迹 history */
export async function fetchTrajectoryHistory(sessionId: string): Promise<HistoryEntry[]> {
  const { data } = await api.get(`/trajectories/${sessionId}/detail/history`)
  return data
}

/** 轨迹 info */
export async function fetchTrajectoryInfo(sessionId: string): Promise<Record<string, unknown>> {
  const { data } = await api.get(`/trajectories/${sessionId}/detail/info`)
  return data
}

/** 轨迹原始 JSON 文本 */
export async function fetchTrajectoryRawText(sessionId: string): Promise<string> {
  const { data } = await api.get(`/trajectories/${sessionId}/detail/raw`, {
    responseType: 'text',
    transformResponse: value => value,
  })
  return data as string
}

/** 更新轨迹标注 */
export async function updateTrajectory(sessionId: string, update: Record<string, unknown>) {
  const { data } = await api.patch(`/trajectories/${sessionId}`, update)
  return data
}

/** 批量更新轨迹标注 */
export async function batchUpdateTrajectories(update: Record<string, unknown>): Promise<TrajectoryBatchUpdateResponse> {
  const { data } = await api.patch('/trajectories/batch', update)
  return data
}

/** 删除轨迹 */
export async function deleteTrajectory(sessionId: string) {
  const { data } = await api.delete(`/trajectories/${sessionId}`)
  return data
}

/** 统计总览 */
export async function fetchStatsOverview(params: Record<string, unknown>): Promise<StatsOverviewResponse> {
  const { data } = await api.get('/stats/overview', { params })
  return data
}

/** 趋势统计 */
export async function fetchStatsTrends(params: Record<string, unknown>): Promise<TrendsResponse> {
  const { data } = await api.get('/stats/trends', { params })
  return data
}

/** 工具分布 */
export async function fetchToolDistribution(params: Record<string, unknown>): Promise<DistributionResponse> {
  const { data } = await api.get('/stats/tools', { params })
  return data
}

/** 模型分布 */
export async function fetchModelDistribution(params: Record<string, unknown>): Promise<DistributionResponse> {
  const { data } = await api.get('/stats/models', { params })
  return data
}

/** 成本分析 */
export async function fetchCostStats(params: Record<string, unknown>): Promise<CostStatsResponse> {
  const { data } = await api.get('/stats/cost', { params })
  return data
}

/** 导出轨迹 zip */
export async function exportTrajectories(sessionIds: string[]): Promise<Blob> {
  const { data } = await api.post(
    '/export/trajectories',
    { session_ids: sessionIds },
    { responseType: 'blob' }
  )
  return data
}

/** 导出 SFT JSONL */
export async function exportSFT(payload: Record<string, unknown>): Promise<Blob> {
  const { data } = await api.post('/export/sft', payload, { responseType: 'blob' })
  return data
}

/** 健康检查 */
export async function healthCheck() {
  const { data } = await api.get('/health')
  return data
}

export default api
