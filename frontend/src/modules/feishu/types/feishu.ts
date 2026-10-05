// 对应 backend/app/modules/feishu/schemas.py。任何字段都不含 token 明文或密文。
export type FeishuCallItem = {
  id: number
  created_at: string
  user_id: number | null
  device_id: string
  tool: string
  // 文档 / 知识库节点 token；搜索没有目标，为空串
  target_token: string
  outcome: string
  error_code: string | null
  latency_ms: number
  request_id: string | null
}

export type FeishuGrantItem = {
  user_id: number
  scope: string
  access_expires_at: string
  refresh_expires_at: string | null
  granted_at: string
  updated_at: string
  version: number
  // 授权快满 365 天，需要本人重新登录
  reauth_soon: boolean
}

export type FeishuGrantListResponse = {
  // 服务端是否配了 TOKEN_ENC_KEY；没配则 token 不落库、远程 MCP 返回 503
  enabled: boolean
  items: FeishuGrantItem[]
}

export type FeishuCallFilters = {
  user_id?: number
  device_id?: string
  outcome?: string
  since?: string
}
