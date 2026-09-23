/** 管理台审计视图。events 只读，没有写入动作。 */

export type EventItem = {
  id: number
  event_name: string
  device_id: string
  org_id: string
  team_id: string
  session_id: string | null
  client_ts: number
  received_at: string
  metadata: Record<string, unknown>
}

export type DailyBucket = {
  date: string
  count: number
}

export type EventListResponse = {
  total: number
  items: EventItem[]
  daily: DailyBucket[]
}

export type EventRejectItem = {
  event_name: string
  reason: string
  count: number
  first_seen_at: string
  last_seen_at: string
}

export type EventRejectListResponse = {
  total: number
  items: EventRejectItem[]
}

export type SessionCoverageItem = {
  session_id: string | null
  has_trajectory: boolean
  event_count: number
  policy_enforced: number
  guardrail_triggered: number
  context_assembled: number
  device_id: string
  first_client_ts: number | null
  last_client_ts: number | null
}

export type SessionCoverageResponse = {
  sessions_with_events: number
  with_trajectory: number
  without_trajectory: number
  trajectory_without_events: number
  items: SessionCoverageItem[]
}

export type PolicyAuditItem = {
  device_id: string
  org_id: string
  team_id: string
  policy_applied: number
  policy_none_or_error: number
  guardrail_total: number
  guardrail_true_positive: number
  guardrail_false_positive: number
  guardrail_unknown: number
  permission_deny: number
  last_received_at: string | null
}

export type PolicyAuditResponse = {
  since: string
  items: PolicyAuditItem[]
}

export type EventListParams = {
  session_id?: string
  device_id?: string
  event_name?: string
  org_id?: string
  since?: string
  limit?: number
}
