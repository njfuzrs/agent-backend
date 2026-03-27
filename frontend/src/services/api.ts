/** API 客户端 */

import axios from 'axios'
import type {
  CompareGroupDetailResponse,
  CompareGroupListResponse,
  CompareRadarResponse,
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
const api = axios.create({
  baseURL: apiBasePath,
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

/** 对比组列表 */
export async function fetchCompareGroups(): Promise<CompareGroupListResponse> {
  const { data } = await api.get('/compare/groups')
  return data
}

/** 创建对比组 */
export async function createCompareGroup(payload: Record<string, unknown>): Promise<CompareGroupDetailResponse> {
  const { data } = await api.post('/compare/groups', payload)
  return data
}

/** 删除对比组 */
export async function deleteCompareGroup(groupId: number) {
  const { data } = await api.delete(`/compare/groups/${groupId}`)
  return data
}

/** 对比组详情 */
export async function fetchCompareGroupDetail(groupId: number): Promise<CompareGroupDetailResponse> {
  const { data } = await api.get(`/compare/groups/${groupId}`)
  return data
}

/** 添加轨迹到对比组 */
export async function addCompareGroupItems(groupId: number, sessionIds: string[]) {
  const { data } = await api.post(`/compare/groups/${groupId}/items`, { session_ids: sessionIds })
  return data
}

/** 从对比组移除轨迹 */
export async function removeCompareGroupItem(groupId: number, trajectoryId: number) {
  const { data } = await api.delete(`/compare/groups/${groupId}/items/${trajectoryId}`)
  return data
}

/** 雷达图数据 */
export async function fetchCompareRadar(groupId: number): Promise<CompareRadarResponse> {
  const { data } = await api.get(`/compare/groups/${groupId}/radar`)
  return data
}

/** 健康检查 */
export async function healthCheck() {
  const { data } = await api.get('/health')
  return data
}

export default api
