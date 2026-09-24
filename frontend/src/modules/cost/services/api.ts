import api from '../../trajectory/services/api'
import type {
  BudgetAuditItem,
  BudgetEnforcement,
  BudgetEvaluate,
  BudgetItem,
  BudgetPeriod,
  BudgetScopeType,
  UsageByScopeResponse,
  UsageLedgerListResponse,
} from '../types/cost'

// 管理台只打两条前缀：/usage/ledger/**（只读）与 /budgets/**（可写）。
//
// **本文件不得出现 `POST /usage/ledger`。** 那是上报口，走设备凭据，浏览器没有；
// 账本能写就不是账本。管理台对它只读，和 event 模块同一条纪律。
//
// 也不打 /ctl/**：那是客户端设备通道。/ctl/budget 的下发形状与本页无关，
// 预览命中走 /budgets/evaluate（cookie 会话）。
//
// 冻结区 `GET /stats/cost` 是另一份事实（按轨迹聚合、没有 device 归属），
// 本页一律不碰。对不上时以账本为准。

export async function fetchUsageByScope(params: {
  period: string
  period_key?: string
  org_id?: string
}): Promise<UsageByScopeResponse> {
  const { data } = await api.get('/usage/ledger/stats/by-scope', { params })
  return data
}

export async function fetchUsageLedger(params: {
  device_id?: string
  org_id?: string
  since?: string
  limit?: number
} = {}): Promise<UsageLedgerListResponse> {
  const { data } = await api.get('/usage/ledger', { params })
  return data
}

export async function fetchBudgets(params: Record<string, string> = {}): Promise<BudgetItem[]> {
  const { data } = await api.get('/budgets', { params })
  return data.items
}

export async function createBudget(payload: {
  scope_type: BudgetScopeType
  scope_id: string
  org_id: string
  period: BudgetPeriod
  limit_usd: number
  enforcement: BudgetEnforcement
  reason: string
}): Promise<BudgetItem> {
  const { data } = await api.post('/budgets', payload)
  return data
}

export async function updateBudget(
  id: number,
  payload: {
    limit_usd?: number
    enforcement?: BudgetEnforcement
    period?: BudgetPeriod
    reason: string
  },
): Promise<BudgetItem> {
  const { data } = await api.patch(`/budgets/${id}`, payload)
  return data
}

export async function setBudgetEnabled(id: number, enabled: boolean, reason: string): Promise<BudgetItem> {
  const action = enabled ? 'enable' : 'disable'
  const { data } = await api.post(`/budgets/${id}/${action}`, null, { params: { reason } })
  return data
}

export async function deleteBudget(id: number, reason: string): Promise<{ id: number; deleted: boolean }> {
  const { data } = await api.delete(`/budgets/${id}`, { params: { reason } })
  return data
}

export async function fetchBudgetAudit(budgetId?: number): Promise<BudgetAuditItem[]> {
  const { data } = await api.get('/budgets/audit', { params: budgetId ? { budget_id: budgetId } : {} })
  return data.items
}

/** 预览该设备命中哪一层。204 = 三层都没配，返回 null。 */
export async function evaluateDeviceBudget(deviceId: string): Promise<BudgetEvaluate | null> {
  const { data, status } = await api.get('/budgets/evaluate', {
    params: { device_id: deviceId },
    validateStatus: s => s === 200 || s === 204,
  })
  if (status === 204) return null
  return data
}
