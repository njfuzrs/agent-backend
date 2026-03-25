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
}

export interface HistoryEntry {
  role: 'user' | 'assistant'
  content: string | ContentBlock[]
}

export interface ContentBlock {
  type: string
  text?: string
  name?: string
  input?: Record<string, unknown>
  content?: string | ContentBlock[]
  id?: string
  tool_use_id?: string
}
