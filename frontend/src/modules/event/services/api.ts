import api from '../../trajectory/services/api'
import type {
  EventListParams,
  EventListResponse,
  EventRejectListResponse,
  PolicyAuditResponse,
  SessionCoverageResponse,
} from '../types/event'

// 管理台永远只打 GET /events/**。上报端点 POST /events 走设备凭据，
// 浏览器没有设备凭据；events 表只追加，管理台对它只读。本文件不得出现 POST。

export async function fetchEvents(params: EventListParams = {}): Promise<EventListResponse> {
  const { data } = await api.get('/events', { params })
  return data
}

export async function fetchEventRejects(): Promise<EventRejectListResponse> {
  const { data } = await api.get('/events/rejects')
  return data
}

export async function fetchSessionCoverage(limit = 50): Promise<SessionCoverageResponse> {
  const { data } = await api.get('/events/stats/session-coverage', { params: { limit } })
  return data
}

export async function fetchPolicyAudit(params: { since?: string; org_id?: string } = {}): Promise<PolicyAuditResponse> {
  const { data } = await api.get('/events/stats/policy-audit', { params })
  return data
}
