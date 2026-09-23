import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  Row,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import StatsCard from '../../trajectory/components/StatsCard'
import { DailyBarChart } from '../../trajectory/components/SimpleCharts'
import { fetchOrganizations } from '../../identity/services/api'
import {
  fetchEventRejects,
  fetchEvents,
  fetchPolicyAudit,
  fetchSessionCoverage,
} from '../services/api'
import type {
  EventItem,
  PolicyAuditItem,
  SessionCoverageItem,
} from '../types/event'
import { formatDuration } from '../../../utils/format'

function formatTs(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === '') return '—'
  const parsed = typeof value === 'number' ? dayjs(value) : dayjs(value)
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm:ss') : '—'
}

function utcDateDaysAgo(days: number) {
  const d = new Date()
  d.setUTCDate(d.getUTCDate() - days)
  return d.toISOString().slice(0, 10)
}

// since 用 lastNDates(n)[0]：和柱状图第一根同一天，窗口与图对齐。
function lastNDates(days: number) {
  return Array.from({ length: days }, (_, i) => utcDateDaysAgo(days - 1 - i))
}

function delayMs(receivedAt: string, clientTs: number) {
  const received = dayjs(receivedAt).valueOf()
  if (!Number.isFinite(received) || !Number.isFinite(clientTs)) return null
  return Math.max(received - clientTs, 0)
}

function falsePositiveRatio(row: PolicyAuditItem) {
  const denom = row.guardrail_true_positive + row.guardrail_false_positive
  if (denom === 0) return null
  return row.guardrail_false_positive / denom
}

type DrillTarget = {
  session_id?: string
  device_id?: string
  event_name?: string
}

export default function AuditOverview() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const deviceFromUrl = searchParams.get('device_id') || undefined
  const sessionFromUrl = searchParams.get('session_id') || undefined

  const [onlyTrajNoEvents, setOnlyTrajNoEvents] = useState(false)
  const [onlyEventsNoTraj, setOnlyEventsNoTraj] = useState(false)
  const [drill, setDrill] = useState<DrillTarget | null>(
    sessionFromUrl || deviceFromUrl
      ? { session_id: sessionFromUrl, device_id: deviceFromUrl }
      : null,
  )

  const weekDates = useMemo(() => lastNDates(7), [])
  const since7d = weekDates[0] ?? utcDateDaysAgo(6)

  const { data: weekEvents } = useQuery({
    queryKey: ['events-week', since7d],
    queryFn: () => fetchEvents({ since: since7d, limit: 1 }),
  })

  const { data: rejects } = useQuery({
    queryKey: ['event-rejects'],
    queryFn: fetchEventRejects,
  })

  const { data: coverage, isLoading: coverageLoading } = useQuery({
    queryKey: ['event-session-coverage'],
    queryFn: () => fetchSessionCoverage(50),
  })

  const { data: policyAudit, isLoading: policyLoading } = useQuery({
    queryKey: ['event-policy-audit', since7d],
    queryFn: () => fetchPolicyAudit({ since: since7d }),
  })

  const { data: orgs } = useQuery({
    queryKey: ['identity-organizations'],
    queryFn: fetchOrganizations,
  })

  const { data: drillEvents, isLoading: drillLoading } = useQuery({
    queryKey: ['events-drill', drill],
    queryFn: () =>
      fetchEvents({
        session_id: drill?.session_id,
        device_id: drill?.device_id,
        event_name: drill?.event_name,
        limit: 200,
      }),
    enabled: drill !== null,
  })

  const orgName = (orgId: string) => orgs?.find(o => o.org_id === orgId)?.name ?? orgId

  const openDrill = (target: DrillTarget) => {
    setDrill(target)
    const next = new URLSearchParams(searchParams)
    if (target.session_id) next.set('session_id', target.session_id)
    else next.delete('session_id')
    if (target.device_id) next.set('device_id', target.device_id)
    setSearchParams(next, { replace: true })
  }

  const coverageRows = useMemo(() => {
    let items = coverage?.items ?? []
    if (deviceFromUrl) items = items.filter(row => row.device_id === deviceFromUrl)
    if (!onlyTrajNoEvents && !onlyEventsNoTraj) return items
    return items.filter(row => {
      const trajNoEvents = row.has_trajectory && row.event_count === 0
      const eventsNoTraj = !row.has_trajectory && row.event_count > 0
      if (onlyTrajNoEvents && onlyEventsNoTraj) return trajNoEvents || eventsNoTraj
      if (onlyTrajNoEvents) return trajNoEvents
      return eventsNoTraj
    })
  }, [coverage?.items, deviceFromUrl, onlyTrajNoEvents, onlyEventsNoTraj])

  const policyRows = useMemo(() => {
    const items = policyAudit?.items ?? []
    if (!deviceFromUrl) return items
    return items.filter(row => row.device_id === deviceFromUrl)
  }, [policyAudit?.items, deviceFromUrl])

  const policyColumns: ColumnsType<PolicyAuditItem> = [
    {
      title: '设备',
      dataIndex: 'device_id',
      render: (id: string) => (
        <Button type="link" style={{ padding: 0 }} onClick={() => navigate(`/devices?device_id=${encodeURIComponent(id)}`)}>
          {id}
        </Button>
      ),
    },
    {
      title: 'org / team',
      key: 'org',
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Typography.Text>{orgName(row.org_id)}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {row.team_id || '—'}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: 'policy applied',
      dataIndex: 'policy_applied',
      align: 'right',
      width: 120,
    },
    {
      title: 'policy none/error',
      dataIndex: 'policy_none_or_error',
      align: 'right',
      width: 150,
      render: (value: number) => (value > 0 ? <Tag color="warning">{value}</Tag> : value),
    },
    {
      title: '护栏真报',
      dataIndex: 'guardrail_true_positive',
      align: 'right',
      width: 90,
    },
    {
      title: '护栏误报',
      dataIndex: 'guardrail_false_positive',
      align: 'right',
      width: 90,
    },
    {
      title: '护栏未判定',
      dataIndex: 'guardrail_unknown',
      align: 'right',
      width: 100,
    },
    {
      title: 'permission_deny',
      dataIndex: 'permission_deny',
      align: 'right',
      width: 140,
    },
    {
      title: '最后上报',
      dataIndex: 'last_received_at',
      width: 170,
      render: formatTs,
    },
    {
      title: '下钻',
      key: 'drill',
      width: 80,
      render: (_, row) => (
        <Button size="small" onClick={() => openDrill({ device_id: row.device_id })}>
          事件
        </Button>
      ),
    },
  ]

  const coverageColumns: ColumnsType<SessionCoverageItem> = [
    {
      title: 'session_id',
      dataIndex: 'session_id',
      render: (id: string | null, row) =>
        id && row.has_trajectory ? (
          <Button type="link" style={{ padding: 0 }} onClick={() => navigate(`/trajectories/${id}`)}>
            {id}
          </Button>
        ) : (
          <Typography.Text code>{id || '（无 session）'}</Typography.Text>
        ),
    },
    {
      title: '有轨迹',
      dataIndex: 'has_trajectory',
      width: 80,
      render: (ok: boolean) => (ok ? <Tag color="green">有</Tag> : <Tag>无</Tag>),
    },
    {
      title: '事件条数',
      dataIndex: 'event_count',
      align: 'right',
      width: 90,
      render: (n: number) => (n === 0 ? <Tag color="warning">0</Tag> : n),
    },
    {
      title: 'policy_enforced',
      dataIndex: 'policy_enforced',
      align: 'right',
      width: 130,
    },
    {
      title: 'guardrail_triggered',
      dataIndex: 'guardrail_triggered',
      align: 'right',
      width: 150,
    },
    {
      title: 'context_assembled',
      dataIndex: 'context_assembled',
      align: 'right',
      width: 150,
    },
    {
      title: 'device_id',
      dataIndex: 'device_id',
      ellipsis: true,
    },
    {
      title: '首/末事件',
      key: 'span',
      width: 200,
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {formatTs(row.first_client_ts)}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {formatTs(row.last_client_ts)}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '下钻',
      key: 'drill',
      width: 80,
      render: (_, row) => (
        <Button
          size="small"
          disabled={!row.session_id}
          onClick={() => openDrill({ session_id: row.session_id ?? undefined, device_id: row.device_id || undefined })}
        >
          事件
        </Button>
      ),
    },
  ]

  const drillColumns: ColumnsType<EventItem> = [
    {
      title: '发生 (client_ts)',
      dataIndex: 'client_ts',
      width: 180,
      render: formatTs,
    },
    {
      title: '到达 (received_at)',
      dataIndex: 'received_at',
      width: 180,
      render: formatTs,
    },
    {
      title: '到达延迟',
      key: 'lag',
      width: 110,
      render: (_, row) => {
        const lag = delayMs(row.received_at, row.client_ts)
        if (lag === null) return '—'
        return lag > 60_000 ? <Tag color="warning">{formatDuration(lag)}</Tag> : formatDuration(lag)
      },
    },
    {
      title: '事件名',
      dataIndex: 'event_name',
      width: 180,
      render: (name: string) => <Typography.Text code>{name}</Typography.Text>,
    },
    {
      title: 'metadata',
      dataIndex: 'metadata',
      render: (meta: Record<string, unknown>) => (
        <Collapse
          size="small"
          items={[
            {
              key: 'meta',
              label: Object.keys(meta).length ? `${Object.keys(meta).length} 个字段` : '空',
              children: (
                <pre style={{ margin: 0, whiteSpace: 'pre-wrap', fontSize: 12 }}>
                  {JSON.stringify(meta, null, 2)}
                </pre>
              ),
            },
          ]}
        />
      ),
    },
  ]

  const weekTotal = weekEvents?.total ?? 0
  const trajNoEvents = coverage?.trajectory_without_events ?? 0
  const eventsNoTraj = coverage?.without_trajectory ?? 0
  const rejectedTotal = rejects?.total ?? 0

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message="事件通道要设备凭据，和轨迹用 session_id 值 join"
        description={
          <span>
            上报 <Typography.Text code>POST /traj/api/v1/events</Typography.Text> 要 Bearer 设备凭据，没 enroll
            的机器不报事件（fail-open，客户端不会报错）。事件与轨迹用 <Typography.Text code>session_id</Typography.Text>{' '}
            值 join，<strong>不是外键</strong>；事件通常先到，轨迹在会话结束才上传，所以「有事件无轨迹」在进行中的会话里是正常的。
            磁盘缓存只保 24h（<Typography.Text code>MAX_AGE_MS</Typography.Text>），平台停机超 24h 那段数据永久丢失，导出成功率的分母要排除停机窗口。
            <Typography.Text code>unknown</Typography.Text> 误报标记既不算真报也不算误报。
          </span>
        }
      />

      {deviceFromUrl && (
        <Alert
          type="warning"
          showIcon
          closable
          message={`正在看设备 ${deviceFromUrl}`}
          action={
            <Button
              size="small"
              onClick={() => {
                const next = new URLSearchParams(searchParams)
                next.delete('device_id')
                setSearchParams(next)
                setDrill(current => (current?.device_id === deviceFromUrl ? { ...current, device_id: undefined } : current))
              }}
            >
              清除
            </Button>
          }
        />
      )}

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <StatsCard
            title="近 7 天事件"
            value={String(weekTotal)}
            hint={weekTotal === 0 ? '0 = 通道没接上' : '含补传'}
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatsCard
            title="有轨迹零事件"
            value={String(trajNoEvents)}
            hint={trajNoEvents > 0 ? '>0 = 在采轨迹但没报事件' : '通道看起来接上了'}
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatsCard
            title="有事件无轨迹"
            value={String(eventsNoTraj)}
            hint="进行中的会话正常；持久存在才是积压/崩溃"
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatsCard
            title="rejected 累计"
            value={String(rejectedTotal)}
            hint={rejectedTotal > 0 ? '白名单外的事件名，看下方明细' : '没有被拒的名字'}
          />
        </Col>
      </Row>

      {rejectedTotal > 0 && (
        <Card size="small" title="被拒的事件名">
          <Table
            rowKey="event_name"
            size="small"
            pagination={false}
            dataSource={rejects?.items ?? []}
            columns={[
              {
                title: '名字',
                dataIndex: 'event_name',
                render: (name: string) => <Typography.Text code>{name}</Typography.Text>,
              },
              { title: '原因', dataIndex: 'reason' },
              { title: '次数', dataIndex: 'count', width: 80, align: 'right' },
              { title: '最近一次', dataIndex: 'last_seen_at', width: 180, render: formatTs },
            ]}
          />
        </Card>
      )}

      <Card
        size="small"
        title="按天到达量（received_at）"
        extra={<Typography.Text type="secondary">停机窗口由发版记录人工对照，不做自动推断</Typography.Text>}
      >
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          不放「导出成功率 98%」这种会自己恢复的百分比。柱子按到达日分桶，丢掉的几天会留下缺口。
        </Typography.Paragraph>
        <DailyBarChart
          data={weekDates.map(date => ({
            label: dayjs(date).format('MM-DD'),
            value: weekEvents?.daily?.find(item => item.date === date)?.count ?? 0,
          }))}
        />
      </Card>

      <Card
        title="策略与护栏是不是在拦东西"
        extra={
          <Typography.Text type="secondary">
            窗口 {policyAudit?.since ? dayjs(policyAudit.since).format('MM-DD HH:mm') : '近 7 天'} 起
          </Typography.Text>
        }
      >
        <Typography.Paragraph type="secondary">
          <Typography.Text code>none</Typography.Text>/<Typography.Text code>error</Typography.Text> 必须单列，不能和{' '}
          <Typography.Text code>applied</Typography.Text> 合成「策略拉取次数」——一台策略从未生效的设备会看起来一样活跃。
          <Typography.Text code>unknown</Typography.Text> 既不算真报也不算误报。不做「误报率」这个百分比；如果非要一个数，它必须是{' '}
          <Typography.Text code>误报 / (真报 + 误报)</Typography.Text>，旁边注明 unknown 条数。
          本页数字来自上报数据，受采样与 24h 缓存窗口影响；权威口径是本机 <Typography.Text code>policy-trigger-rate.ts</Typography.Text>。
        </Typography.Paragraph>
        <Table
          rowKey="device_id"
          size="small"
          loading={policyLoading}
          columns={policyColumns}
          dataSource={policyRows}
          pagination={false}
          scroll={{ x: 1200 }}
          expandable={{
            expandedRowRender: row => {
              const ratio = falsePositiveRatio(row)
              return (
                <Typography.Text type="secondary">
                  误报 / (真报 + 误报) = {row.guardrail_false_positive} / (
                  {row.guardrail_true_positive} + {row.guardrail_false_positive}) ={' '}
                  {ratio === null ? '无判定样本' : `${(ratio * 100).toFixed(1)}%`}
                  ，unknown {row.guardrail_unknown} 条不进分母。
                </Typography.Text>
              )
            },
          }}
        />
      </Card>

      <Card
        title="会话覆盖（事件与轨迹的 join）"
        extra={
          <Space>
            <span>
              只看有轨迹零事件{' '}
              <Switch size="small" checked={onlyTrajNoEvents} onChange={setOnlyTrajNoEvents} />
            </span>
            <span>
              只看有事件无轨迹{' '}
              <Switch size="small" checked={onlyEventsNoTraj} onChange={setOnlyEventsNoTraj} />
            </span>
          </Space>
        }
      >
        <Typography.Paragraph type="secondary">
          这两个开关就是本视图的全部价值。有轨迹零事件是「事件通道没接上」的唯一信号；有事件无轨迹在进行中的会话里正常。
        </Typography.Paragraph>
        <Table
          rowKey={row => `${row.session_id ?? 'none'}-${row.device_id}-${row.event_count}`}
          size="small"
          loading={coverageLoading}
          columns={coverageColumns}
          dataSource={coverageRows}
          pagination={false}
          scroll={{ x: 1280 }}
        />
      </Card>

      {drill && (
        <Card
          title="事件明细（下钻终点）"
          extra={
            <Button
              size="small"
              onClick={() => {
                setDrill(null)
                const next = new URLSearchParams(searchParams)
                next.delete('session_id')
                setSearchParams(next, { replace: true })
              }}
            >
              关闭
            </Button>
          }
        >
          <Typography.Paragraph type="secondary">
            {drill.session_id ? `session ${drill.session_id}` : ''}
            {drill.device_id ? ` device ${drill.device_id}` : ''}
            。到达延迟大 = 补传。metadata 原样展示，不做字段级筛选。
          </Typography.Paragraph>
          <Table
            rowKey="id"
            size="small"
            loading={drillLoading}
            columns={drillColumns}
            dataSource={drillEvents?.items ?? []}
            pagination={false}
          />
        </Card>
      )}
    </Space>
  )
}
