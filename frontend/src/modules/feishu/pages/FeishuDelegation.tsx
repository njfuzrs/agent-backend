import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Card, Input, Select, Space, Table, Tag, Tooltip, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import { fetchUsers } from '../../auth/services/api'
import { fetchFeishuCalls, fetchFeishuGrants } from '../services/api'
import type { FeishuCallItem, FeishuGrantItem } from '../types/feishu'

function formatTs(value: string | null | undefined) {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '—'
}

// feishu_call_audit.outcome 的取值，见 backend/app/modules/feishu/service/tools.py 的 OUTCOMES
const OUTCOME_META: Record<string, { label: string; color: string }> = {
  ok: { label: '成功', color: 'green' },
  forbidden: { label: '无权限', color: 'orange' },
  not_found: { label: '不存在', color: 'default' },
  scope_missing: { label: '缺授权 scope', color: 'gold' },
  reauth_required: { label: '需重新登录', color: 'red' },
  not_logged_in: { label: '设备未登录', color: 'red' },
  unavailable: { label: '飞书不可用', color: 'volcano' },
  bad_request: { label: '参数错误', color: 'default' },
  unsupported: { label: '不支持', color: 'default' },
}

const TOOL_LABELS: Record<string, string> = {
  feishu_doc_read: '读文档',
  feishu_doc_search: '搜文档',
  feishu_wiki_node: '知识库节点',
}

const SINCE_OPTIONS = [
  { value: 1, label: '近 1 天' },
  { value: 7, label: '近 7 天' },
  { value: 30, label: '近 30 天' },
  { value: 0, label: '全部' },
]

// 后端 created_at 是 utc_now_iso()（+00:00 结尾），按字符串比较，这里生成同样格式
function sinceIso(days: number): string | undefined {
  if (!days) return undefined
  return dayjs().subtract(days, 'day').toDate().toISOString().replace('Z', '+00:00')
}

export default function FeishuDelegation() {
  const [userId, setUserId] = useState<number | undefined>(undefined)
  const [outcome, setOutcome] = useState<string | undefined>(undefined)
  const [deviceId, setDeviceId] = useState('')
  const [sinceDays, setSinceDays] = useState(7)

  const { data: users } = useQuery({ queryKey: ['auth-users'], queryFn: fetchUsers })
  const { data: grants, isLoading: grantsLoading } = useQuery({ queryKey: ['feishu-grants'], queryFn: fetchFeishuGrants })
  const { data: calls, isLoading: callsLoading, refetch } = useQuery({
    queryKey: ['feishu-calls', userId, outcome, deviceId, sinceDays],
    queryFn: () =>
      fetchFeishuCalls({ user_id: userId, outcome, device_id: deviceId.trim() || undefined, since: sinceIso(sinceDays) }),
  })

  const userName = (id: number | null) => {
    if (id === null) return '—'
    const u = users?.find(x => x.id === id)
    return u ? u.name || u.union_id : `#${id}`
  }

  const grantColumns: ColumnsType<FeishuGrantItem> = [
    { title: '用户', dataIndex: 'user_id', render: userName },
    {
      title: '授权 scope',
      dataIndex: 'scope',
      render: (s: string) => (
        <Space size={[4, 4]} wrap>
          {s.split(/\s+/).filter(Boolean).map(x => <Tag key={x}>{x}</Tag>)}
        </Space>
      ),
    },
    {
      title: '授权时间',
      dataIndex: 'granted_at',
      render: (v: string, r) => (
        <Space>
          {formatTs(v)}
          {r.reauth_soon && (
            <Tooltip title="飞书授权满 365 天必须重新授权，请本人重新 sid-code login">
              <Tag color="red">快到期</Tag>
            </Tooltip>
          )}
        </Space>
      ),
    },
    { title: 'access 过期', dataIndex: 'access_expires_at', render: formatTs },
    { title: 'refresh 过期', dataIndex: 'refresh_expires_at', render: formatTs },
    { title: '最近刷新', dataIndex: 'updated_at', render: formatTs },
    { title: '刷新次数', dataIndex: 'version' },
  ]

  const callColumns: ColumnsType<FeishuCallItem> = [
    { title: '时间', dataIndex: 'created_at', render: formatTs, width: 150 },
    { title: '用户', dataIndex: 'user_id', render: userName },
    {
      title: '设备',
      dataIndex: 'device_id',
      render: (v: string) => (
        <Typography.Link onClick={() => setDeviceId(v)} ellipsis style={{ maxWidth: 160 }}>
          {v}
        </Typography.Link>
      ),
    },
    { title: '工具', dataIndex: 'tool', render: (t: string) => TOOL_LABELS[t] || t },
    {
      title: '文档 token',
      dataIndex: 'target_token',
      render: (v: string) => (v ? <Typography.Text code copyable>{v}</Typography.Text> : '—'),
    },
    {
      title: '结果',
      dataIndex: 'outcome',
      render: (o: string, r) => {
        const meta = OUTCOME_META[o] || { label: o, color: 'default' }
        return (
          <Space size={4}>
            <Tag color={meta.color}>{meta.label}</Tag>
            {r.error_code && <Typography.Text type="secondary">{r.error_code}</Typography.Text>}
          </Space>
        )
      },
    },
    { title: '耗时', dataIndex: 'latency_ms', render: (v: number) => `${v} ms`, width: 90 },
  ]

  const hasFilter = userId !== undefined || outcome !== undefined || deviceId !== ''

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Card title="飞书委托授权">
        <Typography.Paragraph type="secondary">
          员工飞书登录后，服务端加密保存其 user_access_token，远程 MCP 以员工本人的飞书权限读文档，token 不出服务端。
          这里只展示授权状态，不含任何 token 内容。
        </Typography.Paragraph>
        {grants && !grants.enabled && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message="委托授权未启用"
            description="服务端没有配置 TOKEN_ENC_KEY：登录时不保存飞书 token，设备调用远程 MCP 会返回 503。"
          />
        )}
        <Table
          rowKey="user_id"
          loading={grantsLoading}
          dataSource={grants?.items}
          columns={grantColumns}
          pagination={false}
          locale={{ emptyText: '还没有人授权' }}
        />
      </Card>
      <Card
        title="调用审计"
        extra={<Button size="small" onClick={() => refetch()}>刷新</Button>}
      >
        <Space wrap style={{ marginBottom: 16 }}>
          <Select
            allowClear
            showSearch
            placeholder="用户"
            style={{ width: 180 }}
            value={userId}
            onChange={setUserId}
            optionFilterProp="label"
            options={users?.map(u => ({ value: u.id, label: u.name || u.union_id }))}
          />
          <Select
            allowClear
            placeholder="结果"
            style={{ width: 150 }}
            value={outcome}
            onChange={setOutcome}
            options={Object.entries(OUTCOME_META).map(([value, m]) => ({ value, label: m.label }))}
          />
          <Input
            allowClear
            placeholder="设备 ID"
            style={{ width: 220 }}
            value={deviceId}
            onChange={e => setDeviceId(e.target.value)}
          />
          <Select style={{ width: 120 }} value={sinceDays} onChange={setSinceDays} options={SINCE_OPTIONS} />
          {hasFilter && (
            <Button
              onClick={() => {
                setUserId(undefined)
                setOutcome(undefined)
                setDeviceId('')
              }}
            >
              清除筛选
            </Button>
          )}
        </Space>
        <Typography.Paragraph type="secondary">
          每次远程 MCP 调用一行，只记文档 token，不记文档内容和搜索词。最多显示最近 500 条。
        </Typography.Paragraph>
        <Table
          rowKey="id"
          size="small"
          loading={callsLoading}
          dataSource={calls}
          columns={callColumns}
          pagination={{ pageSize: 20 }}
        />
      </Card>
    </Space>
  )
}
