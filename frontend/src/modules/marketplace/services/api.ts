import api from '../../trajectory/services/api'
import type {
  MarketAuditItem,
  MarketDownloadItem,
  MarketDownloadStatsItem,
  MarketItem,
  MarketKind,
  MarketVersionItem,
} from '../types/marketplace'

// 管理台只走 /marketplace/**（cookie 会话）。/ctl/marketplace/** 是设备通道，管理台不碰。

export async function fetchMarketItems(): Promise<MarketItem[]> {
  const { data } = await api.get('/marketplace/items')
  return data.items
}

export async function createMarketItem(payload: {
  name: string
  kind: MarketKind
  description: string
  maintainer: string
  org_id: string
  team_id: string
}): Promise<MarketItem> {
  const { data } = await api.post('/marketplace/items', payload)
  return data
}

export async function updateMarketItem(
  name: string,
  payload: Partial<{ kind: MarketKind; description: string; maintainer: string; org_id: string; team_id: string }>,
): Promise<MarketItem> {
  const { data } = await api.patch(`/marketplace/items/${encodeURIComponent(name)}`, payload)
  return data
}

export async function uploadMarketVersion(name: string, file: File): Promise<MarketVersionItem> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post(`/marketplace/items/${encodeURIComponent(name)}/versions`, form)
  return data
}

export async function setVersionStatus(
  name: string,
  version: string,
  action: 'publish' | 'yank',
  reason: string,
): Promise<MarketVersionItem> {
  const { data } = await api.post(
    `/marketplace/items/${encodeURIComponent(name)}/versions/${encodeURIComponent(version)}/${action}`,
    null,
    { params: { reason } },
  )
  return data
}

export async function fetchMarketAudit(name?: string): Promise<MarketAuditItem[]> {
  const { data } = await api.get('/marketplace/audit', { params: name ? { name } : {} })
  return data.items
}

export async function fetchMarketDownloads(name?: string): Promise<MarketDownloadItem[]> {
  const { data } = await api.get('/marketplace/downloads', { params: name ? { name } : {} })
  return data.items
}

export async function fetchMarketDownloadStats(days = 30): Promise<MarketDownloadStatsItem[]> {
  const { data } = await api.get('/marketplace/downloads/stats', { params: { days } })
  return data.items
}
