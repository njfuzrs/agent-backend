/** 管理台遥控会话。token 只在签发响应里出现一次，列表项没有这个字段。 */

export type BridgeState = 'waiting' | 'paired' | 'disconnected' | 'expired'

export type BridgeSessionItem = {
  id: string
  device_id: string
  org_id: string
  state: BridgeState
  ver: string | null
  cwd_basename: string | null
  created_at: string
  expires_at: string
}

export type BridgeSessionCounts = {
  paired: number
  waiting: number
  disconnect_24h: number
}

export type BridgeSessionList = {
  items: BridgeSessionItem[]
  total: number
  counts: BridgeSessionCounts
}

/** 签发响应。session_token 明文只此一次，不进列表、不进 localStorage。 */
export type BridgeControllerIssued = {
  session_id: string
  session_token: string
  ws_url: string
  expires_at: string
}

/** 入向闭集之外的握手帧。中继在 Upgrade 之后认它，业务闭集里没有。 */
export type BridgeAuthOk = {
  type: 'auth_ok'
  session_id: string
}

export type BridgeLiveLine = {
  key: string
  kind: 'text' | 'status' | 'tool' | 'expired'
  text: string
  at: number
}

export type BridgePendingPermission = {
  id: string
  toolName: string
  /** 客户端 Date.now()。倒计时用它 + 60s，收到 permission_expired 以服务端为准。 */
  timestamp: number
}
