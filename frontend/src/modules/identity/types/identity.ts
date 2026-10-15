export type OrganizationItem = {
  org_id: string
  name: string
  created_at: string
  device_count: number
}

export type TeamItem = {
  org_id: string
  team_id: string
  name: string
  created_at: string
  device_count: number
}

export type IdentityAuditItem = {
  id: number
  action: string
  target_type: 'org' | 'team' | 'device'
  target_id: string
  org_id: string
  detail: Record<string, unknown>
  reason: string
  actor: string
  created_at: string
  request_id: string | null
}

export type DeviceMoveResponse = {
  device_id: string
  org_id: string
  team_id: string
  changed: boolean
}

export type EnrollCodeCreated = {
  code: string
  org_id: string
  team_id: string
  expires_at: string
  note: string
}

export type EnrollCodeItem = {
  id: number
  org_id: string
  team_id: string
  expires_at: string
  used_at: string | null
  created_at: string
  created_by: string
  note: string
}

export type DeviceListItem = {
  device_id: string
  org_id: string
  team_id: string
  user_id: string
  user_ref: number | null
  platform: string
  ver: string
  last_seen_at: string | null
  created_at: string
  credential_expires_at: string | null
  revoked: boolean
}

export type DeviceListResponse = {
  total: number
  page: number
  page_size: number
  items: DeviceListItem[]
}
