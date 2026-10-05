// 与 backend/app/modules/marketplace/schemas.py 同构

export type MarketKind = 'plugin' | 'skill' | 'mcp'
export type VersionStatus = 'draft' | 'published' | 'yanked'

export type McpServerSummary = {
  name: string
  type: string
  url?: string
  command?: string
  auth?: string
}

export type MarketComponents = {
  skills: string[]
  commands: string[]
  agents: string[]
  hooks: string[]
  mcpServers: McpServerSummary[]
}

export type MarketVersionItem = {
  id: number
  version: string
  status: VersionStatus
  sha256: string
  size_bytes: number
  manifest: Record<string, unknown>
  components: MarketComponents
  created_at: string
  created_by: string
  published_at: string | null
  published_by: string | null
  yanked_at: string | null
}

export type MarketItem = {
  id: number
  name: string
  kind: MarketKind
  description: string
  maintainer: string
  org_id: string
  team_id: string
  created_at: string
  updated_at: string
  created_by: string
  latest_version: string | null
  versions: MarketVersionItem[]
  download_count: number
}

export type MarketAuditItem = {
  id: number
  item_id: number | null
  item_name: string
  version: string
  action: 'create' | 'update' | 'upload' | 'publish' | 'yank'
  reason: string
  actor: string
  detail: Record<string, unknown>
  created_at: string
  request_id: string | null
}

export type MarketDownloadItem = {
  id: number
  created_at: string
  item_name: string
  version: string
  device_id: string
  org_id: string
  user_ref: number | null
}

export type MarketDownloadStatsItem = {
  item_name: string
  user_ref: number | null
  devices: number
  downloads: number
  last_at: string
}
