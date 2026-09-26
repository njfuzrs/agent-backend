import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Col,
  Input,
  Modal,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import StatsCard from '../../trajectory/components/StatsCard'
import { fetchOrganizations } from '../../identity/services/api'
import { disconnectBridgeSession, fetchBridgeSessions, issueControllerToken } from '../services/api'
import type {
  BridgeLiveLine,
  BridgePendingPermission,
  BridgeSessionItem,
  BridgeState,
} from '../types/bridge'

// 客户端 PermissionProxy 的超时。本地倒计时用请求帧的 timestamp + 这个值；
// 收到 permission_expired 以 CLI 为准，倒计时只是还没收到时的提示。
const PERMISSION_TIMEOUT_MS = 60_000

const STATE_LABEL: Record<BridgeState, string> = {
  waiting: '等待中',
  paired: '已配对',
  disconnected: '已断开',
  expired: '已过期',
}

const STATE_COLOR: Record<BridgeState, string> = {
  waiting: 'gold',
  paired: 'green',
  disconnected: 'default',
  expired: 'default',
}

function formatTs(value: string | null | undefined) {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '—'
}

function shortId(value: string) {
  return value.length > 12 ? `${value.slice(0, 8)}…` : value
}

function errorText(err: unknown, fallback: string) {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: string }
    if (first?.msg) return first.msg
  }
  return fallback
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** 浏览器里的 http(s) 页面开不了页内相对的 ws 地址，按当前页面协议拼绝对地址。 */
function toWebSocketUrl(wsUrl: string): string {
  if (wsUrl.startsWith('ws://') || wsUrl.startsWith('wss://')) return wsUrl
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}${wsUrl.startsWith('/') ? '' : '/'}${wsUrl}`
}

function statusText(data: unknown): { kind: BridgeLiveLine['kind']; text: string } | null {
  if (!isRecord(data)) return null
  if (data.ping === true) return null
  const status = typeof data.status === 'string' ? data.status : ''
  if (status === 'permission_expired') {
    const requestId = typeof data.request_id === 'string' ? data.request_id : ''
    return { kind: 'expired', text: requestId ? `审批已过期（${requestId}）` : '审批已过期' }
  }
  if (status === 'pong') return { kind: 'status', text: 'pong' }
  if (status === 'aborted') return { kind: 'status', text: '本轮已中断' }
  if (status) return { kind: 'status', text: status }
  return null
}

function toolNameOf(data: unknown): string {
  if (!isRecord(data)) return '未知工具'
  const name = data.toolName
  return typeof name === 'string' && name.trim() ? name : '未知工具'
}

type LiveSocket = {
  sessionId: string
  deviceId: string
  ws: WebSocket
  /** 首帧 auth 发出去之后才认业务帧。auth_ok 之前的内容丢掉。 */
  authed: boolean
}

export default function BridgeSessions() {
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const deviceFromUrl = searchParams.get('device_id') || undefined

  const [stateFilter, setStateFilter] = useState<BridgeState | undefined>()
  const [orgFilter, setOrgFilter] = useState<string | undefined>()
  const [disconnectTarget, setDisconnectTarget] = useState<BridgeSessionItem | null>(null)
  const [reason, setReason] = useState('')

  const [active, setActive] = useState<BridgeSessionItem | null>(null)
  const [link, setLink] = useState<'connecting' | 'open' | 'closed'>('closed')
  const [lines, setLines] = useState<BridgeLiveLine[]>([])
  const [pending, setPending] = useState<BridgePendingPermission[]>([])
  const [draft, setDraft] = useState('')
  const [now, setNow] = useState(() => Date.now())

  const socketRef = useRef<LiveSocket | null>(null)
  const lineSeq = useRef(0)
  const frameSeq = useRef(0)
  // 进入会话是异步的。快速连点两次时，先发出的那次返回后不能把后一次的连接盖掉。
  const enterSeq = useRef(0)

  const { data: orgs } = useQuery({
    queryKey: ['identity-organizations'],
    queryFn: fetchOrganizations,
  })

  const listParams = useMemo(() => {
    const params: { state?: string; org_id?: string; device_id?: string } = {}
    if (stateFilter) params.state = stateFilter
    if (orgFilter) params.org_id = orgFilter
    if (deviceFromUrl) params.device_id = deviceFromUrl
    return params
  }, [stateFilter, orgFilter, deviceFromUrl])

  const { data, isLoading } = useQuery({
    queryKey: ['bridge-sessions', listParams],
    queryFn: () => fetchBridgeSessions(listParams),
    refetchInterval: active ? false : 10_000,
  })

  const counts = data?.counts

  useEffect(() => {
    if (pending.length === 0) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [pending.length])

  const closeSocket = () => {
    const current = socketRef.current
    socketRef.current = null
    if (current && current.ws.readyState < WebSocket.CLOSING) {
      current.ws.close()
    }
  }

  useEffect(() => closeSocket, [])

  const pushLine = (kind: BridgeLiveLine['kind'], text: string, at = Date.now()) => {
    lineSeq.current += 1
    setLines(prev => [...prev.slice(-199), { key: String(lineSeq.current), kind, text, at }])
  }

  const dropPending = (id: string) => {
    setPending(prev => prev.filter(item => item.id !== id))
  }

  const handleFrame = (raw: string) => {
    const current = socketRef.current
    if (!current) return
    let message: unknown
    try {
      message = JSON.parse(raw)
    } catch {
      return
    }
    if (!isRecord(message) || typeof message.type !== 'string') return
    if (!current.authed) {
      if (message.type === 'auth_ok') current.authed = true
      return
    }
    if (message.type === 'text') {
      const text = isRecord(message.data) && typeof message.data.text === 'string' ? message.data.text : ''
      if (text) pushLine('text', text)
      return
    }
    if (message.type === 'status') {
      const spoken = statusText(message.data)
      if (!spoken) return
      pushLine(spoken.kind, spoken.text)
      if (spoken.kind === 'expired') {
        // 超时帧的 request_id 是 CLI 自己的序号，和请求帧 id 不是同一个值。
        // 对不上就按「这一批已经过期」清掉，卡片不能一直挂着。
        const requestId = isRecord(message.data) && typeof message.data.request_id === 'string'
          ? message.data.request_id
          : ''
        setPending(prev => (requestId && prev.some(item => item.id === requestId) ? prev.filter(item => item.id !== requestId) : []))
      }
      return
    }
    if (message.type === 'tool_use' || message.type === 'tool_result') {
      // 只显示工具名。input / output 可能含密钥或文件内容，不进直播区。
      pushLine('tool', toolNameOf(message.data))
      return
    }
    if (message.type === 'permission_request') {
      const id = typeof message.id === 'string' ? message.id : ''
      if (!id) return
      const timestamp = typeof message.timestamp === 'number' ? message.timestamp : Date.now()
      setPending(prev =>
        prev.some(item => item.id === id)
          ? prev
          : [...prev, { id, toolName: toolNameOf(message.data), timestamp }],
      )
    }
  }

  const sendFrame = (payload: Record<string, unknown>) => {
    const current = socketRef.current
    if (!current || !current.authed || current.ws.readyState !== WebSocket.OPEN) {
      message.error('中继未连接')
      return false
    }
    current.ws.send(JSON.stringify(payload))
    return true
  }

  const enterMutation = useMutation({
    mutationFn: (row: BridgeSessionItem) => {
      const seq = ++enterSeq.current
      return issueControllerToken(row.id).then(issued => ({ issued, seq }))
    },
    onSuccess: ({ issued, seq }, row) => {
      // 后一次进入已经发出。先回来的这次不能把新连接盖掉，也不能再关它。
      if (seq !== enterSeq.current) return
      closeSocket()
      setActive(row)
      setLines([])
      setPending([])
      setDraft('')
      setLink('connecting')
      let ws: WebSocket
      try {
        ws = new WebSocket(toWebSocketUrl(issued.ws_url))
      } catch {
        setLink('closed')
        message.error('中继地址无效')
        return
      }
      const live: LiveSocket = { sessionId: row.id, deviceId: row.device_id, ws, authed: false }
      socketRef.current = live
      ws.onopen = () => {
        if (socketRef.current !== live) return
        setLink('open')
        // token 只在这一帧里用。不进 state、不进 localStorage。
        ws.send(JSON.stringify({ type: 'auth', token: issued.session_token, role: 'controller' }))
      }
      ws.onmessage = event => {
        if (socketRef.current !== live || typeof event.data !== 'string') return
        handleFrame(event.data)
      }
      ws.onclose = () => {
        if (socketRef.current !== live) return
        socketRef.current = null
        setLink('closed')
      }
      ws.onerror = () => {
        if (socketRef.current !== live) return
        setLink('closed')
      }
    },
    onError: err => message.error(errorText(err, '进入失败')),
  })

  const enter = (row: BridgeSessionItem) => {
    enterMutation.mutate(row)
  }

  const leave = () => {
    enterSeq.current += 1
    closeSocket()
    setActive(null)
    setLink('closed')
    setLines([])
    setPending([])
    setDraft('')
  }

  const disconnectMutation = useMutation({
    mutationFn: ({ id, why }: { id: string; why: string }) => disconnectBridgeSession(id, why),
    onSuccess: async (_data, vars) => {
      message.success('已断开')
      if (active?.id === vars.id) leave()
      setDisconnectTarget(null)
      setReason('')
      await queryClient.invalidateQueries({ queryKey: ['bridge-sessions'] })
    },
    onError: err => message.error(errorText(err, '断开失败')),
  })

  const respond = (item: BridgePendingPermission, allowed: boolean) => {
    const sent = sendFrame({
      type: 'permission_response',
      id: item.id,
      data: { allowed },
    })
    if (sent) dropPending(item.id)
  }

  const nextFrameId = (prefix: string) => {
    frameSeq.current += 1
    return `${prefix}-${frameSeq.current}`
  }

  const sendMessage = () => {
    const text = draft.trim()
    if (!text) return
    const sent = sendFrame({ type: 'user_message', id: nextFrameId('msg'), data: { text } })
    if (sent) setDraft('')
  }

  const columns: ColumnsType<BridgeSessionItem> = [
    {
      title: '设备',
      dataIndex: 'device_id',
      render: (id: string) => (
        <Link to={`/devices?device_id=${encodeURIComponent(id)}`}>
          <Typography.Text code>{shortId(id)}</Typography.Text>
        </Link>
      ),
    },
    { title: '组织', dataIndex: 'org_id', width: 140 },
    { title: '版本', dataIndex: 'ver', width: 110, render: (v: string | null) => v || '—' },
    {
      title: '目录',
      dataIndex: 'cwd_basename',
      width: 140,
      render: (v: string | null) => v || '—',
    },
    {
      title: '状态',
      dataIndex: 'state',
      width: 100,
      render: (state: BridgeState) => <Tag color={STATE_COLOR[state]}>{STATE_LABEL[state] ?? state}</Tag>,
    },
    { title: '创建', dataIndex: 'created_at', width: 160, render: formatTs },
    {
      title: '操作',
      key: 'actions',
      width: 160,
      render: (_, row) => {
        const ended = row.state === 'disconnected' || row.state === 'expired'
        return (
          <Space size="small">
            <Button size="small" disabled={ended} onClick={() => enter(row)} loading={enterMutation.isPending}>
              进入
            </Button>
            <Button size="small" danger disabled={ended} onClick={() => setDisconnectTarget(row)}>
              断开
            </Button>
          </Space>
        )
      },
    },
  ]

  const linkLabel = link === 'open' ? '中继已连接' : link === 'connecting' ? '正在连接' : '中继断开'

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Alert
        type="warning"
        showIcon
        message="遥控会在那台机器上跑命令、改文件"
        description={
          <span>
            这是权限最高的通道。工具确认弹在<strong>这一页</strong>，不在那台机器的终端里。超时 60 秒等于拒绝，状态是「审批过期」，不是 agent 出错。
            CLI 必须是已 enroll 的设备；进入时拿到的 token 只展示一次。本页看不到工具输出全文。
          </span>
        }
      />

      {deviceFromUrl && (
        <Alert
          type="info"
          showIcon
          closable
          message={`只看设备 ${deviceFromUrl}`}
          action={
            <Button
              size="small"
              onClick={() => {
                const next = new URLSearchParams(searchParams)
                next.delete('device_id')
                setSearchParams(next)
              }}
            >
              清除
            </Button>
          }
        />
      )}

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={8}>
          <StatsCard
            title="在线配对"
            value={String(counts?.paired ?? 0)}
            hint={(counts?.paired ?? 0) === 0 ? '0 = 没有人在被遥控（可能正常）' : 'paired'}
          />
        </Col>
        <Col xs={24} sm={8}>
          <StatsCard
            title="等待中"
            value={String(counts?.waiting ?? 0)}
            hint={(counts?.waiting ?? 0) > 0 ? '长时间 >0 = CLI 没连上' : '没有机器在等配对'}
          />
        </Col>
        <Col xs={24} sm={8}>
          <StatsCard
            title="近 24h 强制断开"
            value={String(counts?.disconnect_24h ?? 0)}
            hint={(counts?.disconnect_24h ?? 0) > 0 ? '有人在踢人，或自己在验收' : '没有强制断开'}
          />
        </Col>
      </Row>

      <Card
        title="遥控会话"
        extra={
          <Space>
            <Select
              allowClear
              placeholder="状态"
              style={{ width: 140 }}
              value={stateFilter}
              onChange={value => setStateFilter(value)}
              options={(Object.keys(STATE_LABEL) as BridgeState[]).map(value => ({
                value,
                label: STATE_LABEL[value],
              }))}
            />
            <Select
              allowClear
              showSearch
              placeholder="组织"
              style={{ width: 180 }}
              value={orgFilter}
              onChange={value => setOrgFilter(value)}
              options={(orgs ?? []).map(org => ({ value: org.org_id, label: org.org_id }))}
            />
          </Space>
        }
      >
        <Table
          rowKey="id"
          size="small"
          loading={isLoading}
          columns={columns}
          dataSource={data?.items ?? []}
          pagination={false}
        />
      </Card>

      {active && (
        <Card
          title={
            <Space>
              <span>会话 {shortId(active.id)}</span>
              <Tag color={link === 'open' ? 'green' : 'red'}>{linkLabel}</Tag>
              <Typography.Text type="secondary">设备 {shortId(active.device_id)}</Typography.Text>
            </Space>
          }
          extra={
            <Button size="small" onClick={leave}>
              离开
            </Button>
          }
        >
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            {link === 'closed' && (
              <Alert
                type="error"
                showIcon
                message="中继断开"
                description="管理台不会自动重连。需要的话再点一次进入。"
                action={
                  <Button size="small" onClick={() => enter(active)}>
                    重新进入
                  </Button>
                }
              />
            )}

            <div
              style={{
                minHeight: 160,
                maxHeight: 320,
                overflow: 'auto',
                padding: 12,
                background: '#1f1f1f',
                borderRadius: 6,
              }}
            >
              {lines.length === 0 ? (
                <Typography.Text type="secondary">还没有文本。心跳不显示。</Typography.Text>
              ) : (
                lines.map(line => (
                  <div key={line.key} style={{ marginBottom: 8 }}>
                    <Typography.Text type="secondary" style={{ fontSize: 12, marginRight: 8 }}>
                      {dayjs(line.at).format('HH:mm:ss')}
                    </Typography.Text>
                    {line.kind === 'expired' ? (
                      <Tag color="orange">{line.text}</Tag>
                    ) : line.kind === 'tool' ? (
                      <Typography.Text code>{line.text}</Typography.Text>
                    ) : (
                      <Typography.Text>{line.text}</Typography.Text>
                    )}
                  </div>
                ))
              )}
            </div>

            {pending.map(item => {
              const left = Math.max(0, item.timestamp + PERMISSION_TIMEOUT_MS - now)
              return (
                <Card key={item.id} size="small" title={`待批 · ${item.toolName}`}>
                  <Space>
                    <Button type="primary" size="small" onClick={() => respond(item, true)}>
                      允许
                    </Button>
                    <Button size="small" danger onClick={() => respond(item, false)}>
                      拒绝
                    </Button>
                    <Typography.Text type="secondary">
                      {left > 0 ? `剩余 ${Math.ceil(left / 1000)} 秒` : '本地计时已到，等审批过期'}
                    </Typography.Text>
                  </Space>
                </Card>
              )
            })}

            <Space.Compact style={{ width: '100%' }}>
              <Input
                placeholder="发一句话。没有附件，没有斜杠命令。"
                value={draft}
                onChange={event => setDraft(event.target.value)}
                onPressEnter={sendMessage}
                disabled={link !== 'open'}
              />
              <Button type="primary" onClick={sendMessage} disabled={link !== 'open'}>
                发送
              </Button>
              <Button
                disabled={link !== 'open'}
                onClick={() => sendFrame({ type: 'control', id: nextFrameId('ctl'), data: { command: 'abort' } })}
              >
                中断本轮
              </Button>
            </Space.Compact>
          </Space>
        </Card>
      )}

      <Modal
        open={disconnectTarget !== null}
        title="强制断开"
        okText="断开"
        okButtonProps={{ danger: true }}
        confirmLoading={disconnectMutation.isPending}
        onCancel={() => {
          setDisconnectTarget(null)
          setReason('')
        }}
        onOk={() => {
          if (!disconnectTarget) return
          if (!reason.trim()) {
            message.error('原因必填')
            return
          }
          disconnectMutation.mutate({ id: disconnectTarget.id, why: reason.trim() })
        }}
      >
        <Typography.Paragraph>
          断开后这台机器上的 CLI 不会自动重连。原因进审计。
        </Typography.Paragraph>
        <Input
          placeholder="为什么断开"
          value={reason}
          onChange={event => setReason(event.target.value)}
        />
      </Modal>
    </Space>
  )
}
