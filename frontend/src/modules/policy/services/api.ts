import api from '../../trajectory/services/api'
import type { PolicyAuditItem, PolicyEvaluate, PolicyItem, PolicySettings } from '../types/policy'

// 写口在 /policies/**（cookie 会话），不是 /ctl/policy —— 后者是给客户端读的设备通道。
// 管理台永远不碰 /ctl/**。

export async function fetchPolicies(params: Record<string, string> = {}): Promise<PolicyItem[]> {
  const { data } = await api.get('/policies', { params })
  return data.items
}

export async function createPolicy(payload: {
  scope_type: string
  scope_id: string
  org_id: string
  settings: PolicySettings
  reason: string
}): Promise<PolicyItem> {
  const { data } = await api.post('/policies', payload)
  return data
}

export async function updatePolicy(
  id: number,
  payload: { settings: PolicySettings; reason: string },
): Promise<PolicyItem> {
  const { data } = await api.put(`/policies/${id}`, payload)
  return data
}

export async function setPolicyEnabled(id: number, enabled: boolean, reason: string): Promise<PolicyItem> {
  const action = enabled ? 'enable' : 'disable'
  const { data } = await api.post(`/policies/${id}/${action}`, null, { params: { reason } })
  return data
}

export async function deletePolicy(id: number, reason: string): Promise<{ id: number; deleted: boolean }> {
  const { data } = await api.delete(`/policies/${id}`, { params: { reason } })
  return data
}

export async function fetchPolicyAudit(policyId?: number): Promise<PolicyAuditItem[]> {
  const { data } = await api.get('/policies/audit', { params: policyId ? { policy_id: policyId } : {} })
  return data.items
}

export async function evaluateDevicePolicy(deviceId: string): Promise<PolicyEvaluate | null> {
  const { data, status } = await api.get('/policies/evaluate', {
    params: { device_id: deviceId },
    validateStatus: s => s === 200 || s === 204,
  })
  if (status === 204) return null
  return data
}
