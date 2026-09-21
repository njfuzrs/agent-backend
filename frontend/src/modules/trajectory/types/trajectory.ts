/** 轨迹相关 TypeScript 类型定义 */

export interface TrajectoryListItem {
  session_id: string
  tool_source: string
  model: string
  start_time: string | null
  end_time: string | null
  duration_ms: number | null
  total_steps: number
  total_api_calls: number
  total_tokens: number
  total_cost_usd: number
  exit_status: string
  tools_used: string[]
  first_prompt: string
  quality_status: string
  quality_rating: number | null
  traj_file_size: number
  has_thinking: boolean
  has_sub_agent: boolean
  task_type: string
  project_name: string
  tags: string[]
  user_id: string | null
}

export interface TrajectoryMeta extends TrajectoryListItem {
  tokens_sent: number
  tokens_received: number
  cache_read_tokens: number
  cache_creation_tokens: number
  working_directory: string
  files_edited: string[]
  quality_notes: string
  uploaded_at: string
  updated_at: string
}

export interface TrajectoryListResponse {
  total: number
  page: number
  page_size: number
  items: TrajectoryListItem[]
}

export interface TrajectoryStepsResponse {
  total: number
  offset: number
  limit: number
  items: TrajectoryStep[]
}

export interface TrajectoryStep {
  message_type: 'action' | 'observation'
  role: string
  thought?: string
  action?: string
  tool_name?: string
  tool_input?: Record<string, unknown>
  tool_use_id?: string
  content?: string
  is_error?: boolean
  timestamp?: string
  _orphan?: boolean
}

export interface HistoryEntry {
  role: 'system' | 'user' | 'assistant'
  content: string | ContentBlock[]
  thought?: string
  timestamp?: string
  stop_reason?: string
  message_type?: string
}

export interface ContentBlock {
  type: string
  text?: string
  name?: string
  input?: Record<string, unknown>
  arguments?: Record<string, unknown>
  content?: string | ContentBlock[]
  id?: string
  tool_use_id?: string
  is_error?: boolean
}

export interface DistributionItem {
  name: string
  count: number
  total_tokens: number
  total_cost_usd: number
}

export interface StatsOverviewResponse {
  total_trajectories: number
  total_tokens: number
  total_cost_usd: number
  avg_steps_per_trajectory: number
  avg_cost_per_trajectory: number
  success_rate: number
  tool_source_distribution: Record<string, number>
  model_distribution: Record<string, number>
  quality_distribution: Record<string, number>
}

export interface TrendPoint {
  date: string
  count: number
  total_tokens: number
  total_cost_usd: number
  avg_steps: number
  success_rate: number
}

export interface TrendsResponse {
  granularity: 'day' | 'week'
  data: TrendPoint[]
}

export interface DistributionResponse {
  items: DistributionItem[]
}

export interface CostSeriesItem {
  name: string
  values: number[]
}

export interface CostTimelinePoint {
  date: string
  total_cost_usd: number
  by_tool_source: Record<string, number>
}

export interface CostStatsResponse {
  granularity: 'day' | 'week'
  dates: string[]
  series: CostSeriesItem[]
  totals_by_tool_source: DistributionItem[]
  totals_by_model: DistributionItem[]
  timeline: CostTimelinePoint[]
}

export interface TrajectoryBatchUpdateResponse {
  status: string
  updated_count: number
  session_ids: string[]
}
