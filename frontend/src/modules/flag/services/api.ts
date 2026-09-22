import api from '../../trajectory/services/api'
import type { FlagAuditItem, FlagItem, FlagValue } from '../types/flag'

// 写口在 /flags/**（cookie 会话），不是 /ctl/flags —— 后者是给客户端读的无认证下发端点。
// 管理台永远不碰 /ctl/**。

export async function fetchFlags(): Promise<FlagItem[]> {
  const { data } = await api.get('/flags')
  return data.items
}

export async function createFlag(payload: {
  key: string
  value: FlagValue
  description?: string
  reason?: string
}): Promise<FlagItem> {
  const { data } = await api.post('/flags', payload)
  return data
}

export async function updateFlag(
  key: string,
  payload: { value: FlagValue; description?: string; reason?: string },
): Promise<FlagItem> {
  const { data } = await api.put(`/flags/${encodeURIComponent(key)}`, payload)
  return data
}

export async function setFlagEnabled(key: string, enabled: boolean, reason = ''): Promise<FlagItem> {
  const action = enabled ? 'enable' : 'disable'
  const { data } = await api.post(`/flags/${encodeURIComponent(key)}/${action}`, null, {
    params: { reason },
  })
  return data
}

export async function deleteFlag(key: string, reason = ''): Promise<{ key: string; deleted: boolean }> {
  const { data } = await api.delete(`/flags/${encodeURIComponent(key)}`, { params: { reason } })
  return data
}

export async function fetchFlagAudit(key?: string): Promise<FlagAuditItem[]> {
  const { data } = await api.get('/flags/audit', { params: key ? { key } : {} })
  return data.items
}
