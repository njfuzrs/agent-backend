import api from '../../trajectory/services/api'
import type { AuthAuditItem, UserItem } from '../types/auth'

export async function fetchUsers(): Promise<UserItem[]> {
  const { data } = await api.get('/users')
  return data.items
}

export async function setUserRole(id: number, role: UserItem['role']): Promise<UserItem> {
  const { data } = await api.patch(`/users/${id}/role`, { role })
  return data
}

export async function revokeUser(id: number): Promise<UserItem> {
  const { data } = await api.post(`/users/${id}/revoke`)
  return data
}

export async function restoreUser(id: number): Promise<UserItem> {
  const { data } = await api.post(`/users/${id}/restore`)
  return data
}

export async function fetchAuthAudit(userId?: number): Promise<AuthAuditItem[]> {
  const { data } = await api.get('/users/audit', { params: { user_id: userId, limit: 200 } })
  return data.items
}
