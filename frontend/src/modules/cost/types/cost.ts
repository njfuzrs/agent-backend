/** 管理台成本。账本只读，预算可写。
 *
 * 这里**故意没有**单价类型：`cost_usd` 含标题 / 子代理 / 分类器等辅助调用，
 * `prompt_total` 不含。两者口径不同源，相除是错数，服务端也不返回这种字段。
 * 命中率（cache_hit / prompt_total）可以除 —— 分子分母都是主循环 token。
 */

export type BudgetScopeType = 'device' | 'team' | 'org'
export type BudgetPeriod = 'session' | 'daily' | 'weekly' | 'monthly'
/** 没有 downgrade —— 客户端 loop 没接这条线，做了也是死开关。 */
export type BudgetEnforcement = 'alert' | 'block'

/** by-scope 的一行：一台设备在某周期里的用量。 */
export type UsageByScopeItem = {
  device_id: string
  org_id: string
  team_id: string
  sessions: number
  /** 含影子调用。主列。 */
  cost_usd: number
  /** 无影子时为 null —— 界面显示「—」，不是 $0.00。 */
  side_cost_usd: number | null
  /** 主循环 token。旁边不要写「约 $x/1M」。 */
  prompt_total: number
  cache_hit: number
  output: number
  last_received_at: string | null
}

export type UsageByScopeResponse = {
  period: string
  period_key: string
  items: UsageByScopeItem[]
}

/** 下钻：一条账本 = 一个 (device, session)。上报是 upsert，同会话多次覆盖不累加。 */
export type UsageLedgerItem = {
  id: number
  device_id: string
  org_id: string
  team_id: string
  session_id: string
  /** 客户端**秒** epoch。展示要 ×1000。 */
  ts: number
  received_at: string
  model: string
  provider: string
  prompt_total: number
  cache_hit: number
  cache_write: number
  uncached_input: number
  output: number
  cost_usd: number
  savings_usd: number
  duration_ms: number
  side_input_tokens: number | null
  side_output_tokens: number | null
  side_cost_usd: number | null
  endpoint_host: string | null
  app_version: string | null
  peak_ratio: number | null
}

export type UsageLedgerListResponse = {
  total: number
  items: UsageLedgerItem[]
}

export type BudgetItem = {
  id: number
  scope_type: BudgetScopeType
  scope_id: string
  org_id: string
  period: BudgetPeriod
  limit_usd: number
  enforcement: BudgetEnforcement
  /** 服务端现算，不存表。列表也带上 —— 操作者不能只看 limit 看不到 used。 */
  used_usd: number
  disabled: boolean
  created_at: string
  updated_at: string
  updated_by: string
}

export type BudgetAuditItem = {
  id: number
  budget_id: number | null
  scope_type: string
  scope_id: string
  action: 'create' | 'update' | 'delete' | 'disable' | 'enable' | string
  old: Record<string, unknown> | null
  new: Record<string, unknown> | null
  reason: string
  actor: string
  created_at: string
}

/** 预览命中：该设备会命中哪一层。层级由服务端求值，前端不自己排 device>team>org。 */
export type BudgetEvaluate = {
  layer: BudgetScopeType
  budget_id: number
  scope_id: string
  org_id: string
  period: BudgetPeriod
  period_key: string
  limit_usd: number
  used_usd: number
  enforcement: BudgetEnforcement
}
