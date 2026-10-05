import api from '../../trajectory/services/api'
import type { FeishuCallFilters, FeishuCallItem, FeishuGrantListResponse } from '../types/feishu'

export async function fetchFeishuGrants(): Promise<FeishuGrantListResponse> {
  const { data } = await api.get('/feishu/grants')
  return data
}

export async function fetchFeishuCalls(filters: FeishuCallFilters): Promise<FeishuCallItem[]> {
  const { data } = await api.get('/feishu/calls', { params: { ...filters, limit: 500 } })
  return data.items
}
