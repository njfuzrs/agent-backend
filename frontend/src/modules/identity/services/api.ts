import api from '../../trajectory/services/api'
import type {
  DeviceListResponse,
  DeviceMoveResponse,
  EnrollCodeCreated,
  EnrollCodeItem,
  IdentityAuditItem,
  OrganizationItem,
  TeamItem,
} from '../types/identity'

export async function fetchOrganizations(): Promise<OrganizationItem[]> {
  const { data } = await api.get('/identity/organizations')
  return data.items
}

export async function createOrganization(payload: {
  org_id: string
  name?: string
  reason?: string
}): Promise<OrganizationItem> {
  const { data } = await api.post('/identity/organizations', payload)
  return data
}

export async function renameOrganization(
  orgId: string,
  payload: { name: string; reason: string },
): Promise<OrganizationItem> {
  const { data } = await api.patch(`/identity/organizations/${encodeURIComponent(orgId)}`, payload)
  return data
}

export async function fetchTeams(orgId?: string): Promise<TeamItem[]> {
  const { data } = await api.get('/identity/teams', { params: orgId ? { org_id: orgId } : {} })
  return data.items
}

export async function createTeam(payload: {
  org_id: string
  team_id: string
  name?: string
  reason: string
}): Promise<TeamItem> {
  const { data } = await api.post('/identity/teams', payload)
  return data
}

export async function renameTeam(
  orgId: string,
  teamId: string,
  payload: { name: string; reason: string },
): Promise<TeamItem> {
  const { data } = await api.patch(
    `/identity/organizations/${encodeURIComponent(orgId)}/teams/${encodeURIComponent(teamId)}`,
    payload,
  )
  return data
}

export async function moveDevice(
  deviceId: string,
  payload: { org_id: string; team_id?: string | null; reason: string },
): Promise<DeviceMoveResponse> {
  const { data } = await api.post(`/identity/devices/${encodeURIComponent(deviceId)}/move`, payload)
  return data
}

export async function fetchIdentityAudit(params: Record<string, unknown> = {}): Promise<IdentityAuditItem[]> {
  const { data } = await api.get('/identity/audit', { params })
  return data.items
}

export async function fetchEnrollCodes(): Promise<EnrollCodeItem[]> {
  const { data } = await api.get('/identity/enroll-codes')
  return data.items
}

export async function createEnrollCode(payload: {
  org_id: string
  org_name?: string
  team_id?: string
  team_name?: string
  note?: string
}): Promise<EnrollCodeCreated> {
  const { data } = await api.post('/identity/enroll-codes', payload)
  return data
}

export async function fetchDevices(params: Record<string, unknown> = {}): Promise<DeviceListResponse> {
  const { data } = await api.get('/identity/devices', { params })
  return data
}

export async function revokeDevice(deviceId: string): Promise<{ device_id: string; revoked: boolean }> {
  const { data } = await api.post(`/identity/devices/${deviceId}/revoke`)
  return data
}
