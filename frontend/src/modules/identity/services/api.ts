import api from '../../trajectory/services/api'
import type {
  DeviceListResponse,
  EnrollCodeCreated,
  EnrollCodeItem,
  OrganizationItem,
} from '../types/identity'

export async function fetchOrganizations(): Promise<OrganizationItem[]> {
  const { data } = await api.get('/identity/organizations')
  return data.items
}

export async function createOrganization(payload: { org_id: string; name?: string }): Promise<OrganizationItem> {
  const { data } = await api.post('/identity/organizations', payload)
  return data
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
