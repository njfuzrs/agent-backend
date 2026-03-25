/** API 客户端 */

import axios from 'axios'
import type {
  TrajectoryListResponse,
  TrajectoryMeta,
  TrajectoryStepsResponse,
} from '../types/trajectory'

// 开发环境通过 vite proxy 转发，生产环境通过 nginx 反代
const api = axios.create({
  baseURL: '/api/v1',
  auth: {
    username: localStorage.getItem('auth_user') || 'admin',
    password: localStorage.getItem('auth_pass') || '',
  },
})

// 登录后更新 auth
export function setAuth(username: string, password: string) {
  localStorage.setItem('auth_user', username)
  localStorage.setItem('auth_pass', password)
  api.defaults.auth = { username, password }
}

// 检查是否已设置密码
export function hasAuth(): boolean {
  return !!localStorage.getItem('auth_pass')
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

/** 轨迹 history */
export async function fetchTrajectoryHistory(sessionId: string): Promise<unknown[]> {
  const { data } = await api.get(`/trajectories/${sessionId}/detail/history`)
  return data
}

/** 轨迹 info */
export async function fetchTrajectoryInfo(sessionId: string): Promise<Record<string, unknown>> {
  const { data } = await api.get(`/trajectories/${sessionId}/detail/info`)
  return data
}

/** 更新轨迹标注 */
export async function updateTrajectory(sessionId: string, update: Record<string, unknown>) {
  const { data } = await api.patch(`/trajectories/${sessionId}`, update)
  return data
}

/** 删除轨迹 */
export async function deleteTrajectory(sessionId: string) {
  const { data } = await api.delete(`/trajectories/${sessionId}`)
  return data
}

/** 健康检查 */
export async function healthCheck() {
  const { data } = await api.get('/health')
  return data
}

export default api
