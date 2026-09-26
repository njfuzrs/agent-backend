import api from '../../trajectory/services/api'
import type { BridgeControllerIssued, BridgeSessionList } from '../types/bridge'

// 管理台只打 /bridge/sessions/**（cookie 会话）和浏览器自己开的 WS。
//
// **本文件不得出现 `/ctl/bridge/`。** 那是设备凭据端点，浏览器没有设备凭据。
// 管理台签发的是 controller token，走 /bridge/sessions/{id}/controller-token。
// token 明文只在这次响应里，调用方用完即丢，不许进 localStorage。

export async function fetchBridgeSessions(params: {
  state?: string
  org_id?: string
  device_id?: string
} = {}): Promise<BridgeSessionList> {
  const { data } = await api.get('/bridge/sessions', { params })
  return data
}

export async function issueControllerToken(sessionId: string): Promise<BridgeControllerIssued> {
  const { data } = await api.post(`/bridge/sessions/${sessionId}/controller-token`)
  return data
}

export async function disconnectBridgeSession(sessionId: string, reason: string): Promise<void> {
  await api.post(`/bridge/sessions/${sessionId}/disconnect`, { reason })
}
