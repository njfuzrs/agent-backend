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
  // AI 评分
  ai_score: number | null
  ai_grade: string
  ai_quality_status: string
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

/** AI 评分详情 */
export interface ScoringDetail {
  session_id: string
  ai_score: number | null
  ai_grade: string
  ai_quality_status: string
  rule_score: number | null
  rule_details: Record<string, number>
  rule_flags: string[]
  heuristic_score: number | null
  heuristic_details: Record<string, number>
  heuristic_patterns: string[]
  llm_score: number | null
  llm_details: Record<string, number>
  llm_reasoning: string
  llm_suggested_task_type: string
  llm_eval_model: string
  scored_at: string | null
  score_version: number
}

/** AI 评分统计 */
export interface ScoringStatsResponse {
  status_distribution: Record<string, number>
  grade_distribution: Record<string, number>
  avg_score: number
  total_scored: number
  total_pending: number
}

export interface CompareGroupListItem {
  id: number
  name: string
  description: string
  task_prompt: string
  created_at: string
  item_count: number
  tool_sources: string[]
}

export interface CompareGroupListResponse {
  items: CompareGroupListItem[]
}

export interface CompareTrajectoryItem {
  trajectory_id: number
  session_id: string
  tool_source: string
  model: string
  start_time: string | null
  duration_ms: number | null
  total_steps: number
  total_tokens: number
  total_cost_usd: number
  exit_status: string
  quality_status: string
  first_prompt: string
  tools_used: string[]
  tool_usage: Record<string, number>
}

export interface CompareGroupDetailResponse {
  id: number
  name: string
  description: string
  task_prompt: string
  created_at: string
  item_count: number
  items: CompareTrajectoryItem[]
}

export interface CompareRadarItem {
  trajectory_id: number
  session_id: string
  tool_source: string
  model: string
  values: Array<number | string>
  normalized: number[]
}

export interface CompareRadarResponse {
  group_id: number
  group_name: string
  dimensions: string[]
  items: CompareRadarItem[]
}
