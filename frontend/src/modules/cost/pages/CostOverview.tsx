import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Col,
  Form,
  Input,
  InputNumber,
  Modal,
  Radio,
  Row,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import StatsCard from '../../trajectory/components/StatsCard'
import { fetchOrganizations } from '../../identity/services/api'
import { fetchTrajectories } from '../../trajectory/services/api'
import {
  createBudget,
  deleteBudget,
  evaluateDeviceBudget,
  fetchBudgetAudit,
  fetchBudgets,
  fetchUsageByScope,
  fetchUsageLedger,
  setBudgetEnabled,
  updateBudget,
} from '../services/api'
import type {
  BudgetAuditItem,
  BudgetEnforcement,
  BudgetEvaluate,
  BudgetItem,
  BudgetPeriod,
  BudgetScopeType,
  UsageByScopeItem,
  UsageLedgerItem,
} from '../types/cost'
import { formatTokens } from '../../../utils/format'

// 本页没有单价列。`cost_usd` 含标题 / 子代理 / 分类器等辅助调用，`prompt_total` 不含；
// 相除会得出「我们的单价比网关低一截」的假结论，然后有人去改计价。服务端不返回、
// 这里不算、表格不渲染。命中率（cache_hit / prompt_total）是唯一允许的除法 ——
// 分子分母都是主循环 token。

const PERIODS: { value: BudgetPeriod; label: string }[] = [
  { value: 'monthly', label: '月' },
  { value: 'weekly', label: '周' },
  { value: 'daily', label: '日' },
]

/** 金额。0 要显示 $0.00（有上报但没花钱），不能像 formatCurrency 那样显示 '-'。 */
function money(value: number): string {
  return `$${value.toFixed(value >= 1 ? 2 : 4)}`
}

/** 影子成本缺失 = 客户端没有影子调用，显示「—」；$0.00 会让「无影子」与「影子恰好为 0」混同。 */
function optionalMoney(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : money(value)
}

function formatTs(value: string | null | undefined) {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '—'
}

/** 账本 ts 是**秒** epoch。×1000 再给 dayjs，否则一律显示 1970。 */
function formatEpochSeconds(seconds: number | null | undefined) {
  if (!seconds) return '—'
  return dayjs(seconds * 1000).format('YYYY-MM-DD HH:mm')
}

function hitRate(row: { cache_hit: number; prompt_total: number }): string {
  if (!row.prompt_total) return '—'
  return `${((row.cache_hit / row.prompt_total) * 100).toFixed(1)}%`
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

type BudgetFormValues = {
  scope_type: BudgetScopeType
  org_id: string
  scope_id: string
  period: BudgetPeriod
  limit_usd: number
  enforcement: BudgetEnforcement
  reason: string
}

export default function CostOverview() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const deviceFromUrl = searchParams.get('device_id') || undefined

  const [period, setPeriod] = useState<BudgetPeriod>('monthly')
  const [orgFilter, setOrgFilter] = useState<string | undefined>()
  const [onlyOverBudget, setOnlyOverBudget] = useState(false)
  const [drillDeviceId, setDrillDeviceId] = useState<string | null>(null)

  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<BudgetItem | null>(null)
  const [toggleTarget, setToggleTarget] = useState<{ id: number; enabled: boolean } | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<BudgetItem | null>(null)
  const [actionReason, setActionReason] = useState('')
  const [auditId, setAuditId] = useState<number | null | 'all'>(null)
  const [form] = Form.useForm<BudgetFormValues>()

  const [previewDeviceId, setPreviewDeviceId] = useState('')
  const [previewHit, setPreviewHit] = useState<BudgetEvaluate | null | 'empty'>(null)

  const { data: orgs } = useQuery({
    queryKey: ['identity-organizations'],
    queryFn: fetchOrganizations,
  })

  // period_key 不传：让服务端算当前周期键。前端自己拼 ISO 周会和服务端漂一格。
  const { data: byScope, isLoading: scopeLoading } = useQuery({
    queryKey: ['usage-by-scope', period, orgFilter],
    queryFn: () => fetchUsageByScope({ period, org_id: orgFilter }),
  })

  const { data: budgets, isLoading: budgetsLoading } = useQuery({
    queryKey: ['budgets', orgFilter],
    queryFn: () => fetchBudgets(orgFilter ? { org_id: orgFilter } : {}),
  })

  const { data: audit, isLoading: auditLoading } = useQuery({
    queryKey: ['budget-audit', auditId],
    queryFn: () => fetchBudgetAudit(typeof auditId === 'number' ? auditId : undefined),
    enabled: auditId !== null,
  })

  const { data: ledger, isLoading: ledgerLoading } = useQuery({
    queryKey: ['usage-ledger', drillDeviceId],
    queryFn: () => fetchUsageLedger({ device_id: drillDeviceId ?? undefined, limit: 200 }),
    enabled: drillDeviceId !== null,
  })

  // 账本先到、轨迹后到（进行中的会话）。有轨迹行才让 session_id 可点，与 M4 的 join 同款。
  const { data: drillTrajectories } = useQuery({
    queryKey: ['cost-drill-trajectories', drillDeviceId],
    queryFn: () => fetchTrajectories({ device_id: drillDeviceId, page_size: 100 }),
    enabled: drillDeviceId !== null,
  })

  const trajectorySessions = useMemo(
    () => new Set((drillTrajectories?.items ?? []).map(t => t.session_id)),
    [drillTrajectories?.items],
  )

  const orgName = (orgId: string) => orgs?.find(o => o.org_id === orgId)?.name ?? orgId

  const enabledBudgets = useMemo(() => (budgets ?? []).filter(b => !b.disabled), [budgets])

  const overLimitBudgets = useMemo(
    () => enabledBudgets.filter(b => b.used_usd >= b.limit_usd),
    [enabledBudgets],
  )

  // 超限的 scope_id 集合。设备行只要**任一**覆盖它的层超限就算超 —— 这里不排
  // device > team > org 的优先级（那是服务端求值的事，见「预览命中」），
  // 筛子的目的是「把可能已经被拦/被告警的设备捞出来」，覆盖宽一点是对的。
  const overLimitScopeIds = useMemo(
    () => new Set(overLimitBudgets.map(b => b.scope_id)),
    [overLimitBudgets],
  )

  const scopeRows = useMemo(() => {
    let items = byScope?.items ?? []
    if (deviceFromUrl) items = items.filter(row => row.device_id === deviceFromUrl)
    if (onlyOverBudget) {
      items = items.filter(
        row =>
          overLimitScopeIds.has(row.device_id) ||
          (row.team_id && overLimitScopeIds.has(row.team_id)) ||
          overLimitScopeIds.has(row.org_id),
      )
    }
    return items
  }, [byScope?.items, deviceFromUrl, onlyOverBudget, overLimitScopeIds])

  const totals = useMemo(() => {
    const items = byScope?.items ?? []
    let cost = 0
    let sessions = 0
    let side = 0
    let sideSeen = false
    for (const row of items) {
      cost += row.cost_usd
      sessions += row.sessions
      if (row.side_cost_usd !== null && row.side_cost_usd !== undefined) {
        side += row.side_cost_usd
        sideSeen = true
      }
    }
    return { cost, sessions, side, sideSeen }
  }, [byScope?.items])

  const blockedOverLimit = overLimitBudgets.filter(b => b.enforcement === 'block').length

  const invalidateBudgets = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ['budgets'] }),
      queryClient.invalidateQueries({ queryKey: ['usage-by-scope'] }),
    ])

  const closeForm = () => {
    setCreateOpen(false)
    setEditing(null)
    form.resetFields()
  }

  const saveMutation = useMutation({
    mutationFn: async (values: BudgetFormValues) => {
      const reason = values.reason.trim()
      if (!reason) throw new Error('变更原因必填')
      if (editing) {
        return updateBudget(editing.id, {
          limit_usd: values.limit_usd,
          enforcement: values.enforcement,
          period: values.period,
          reason,
        })
      }
      return createBudget({
        scope_type: values.scope_type,
        scope_id: values.scope_type === 'org' ? values.org_id.trim() : values.scope_id.trim(),
        org_id: values.org_id.trim(),
        period: values.period,
        limit_usd: values.limit_usd,
        enforcement: values.enforcement,
        reason,
      })
    },
    onSuccess: async () => {
      message.success(editing ? '已更新预算' : '已创建预算')
      closeForm()
      await invalidateBudgets()
    },
    onError: err => {
      // 同层同周期已有生效行 → 409，服务端 detail 已经写了「改那一条或先停用」。
      // 原样透出，不要换成「保存失败」—— 那会让人以为是网络抖然后重试到第三次。
      message.error(errorText(err, '保存失败'))
    },
  })

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled, reason }: { id: number; enabled: boolean; reason: string }) =>
      setBudgetEnabled(id, enabled, reason),
    onSuccess: async () => {
      message.success('已更新')
      setToggleTarget(null)
      setActionReason('')
      await invalidateBudgets()
    },
    onError: err => message.error(errorText(err, '操作失败')),
  })

  const deleteMutation = useMutation({
    mutationFn: ({ id, reason }: { id: number; reason: string }) => deleteBudget(id, reason),
    onSuccess: async () => {
      message.success('已删除')
      setDeleteTarget(null)
      setActionReason('')
      await invalidateBudgets()
    },
    onError: err => message.error(errorText(err, '删除失败')),
  })

  const previewMutation = useMutation({
    mutationFn: evaluateDeviceBudget,
    onSuccess: hit => setPreviewHit(hit === null ? 'empty' : hit),
    onError: err => {
      setPreviewHit(null)
      message.error(errorText(err, '预览失败'))
    },
  })

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ scope_type: 'org', period: 'monthly', enforcement: 'alert' })
    setCreateOpen(true)
  }

  const openEdit = (row: BudgetItem) => {
    setEditing(row)
    form.setFieldsValue({
      scope_type: row.scope_type,
      org_id: row.org_id,
      scope_id: row.scope_id,
      period: row.period,
      limit_usd: row.limit_usd,
      enforcement: row.enforcement,
      reason: '',
    })
  }

  /** 切硬拦要二次确认。已经超限时文案必须写明「立即生效」—— 否则会出现
   *  「以为是以后才管用」而一保存就杀掉全公司正在跑的会话。 */
  const submitForm = (values: BudgetFormValues) => {
    if (values.enforcement !== 'block') {
      saveMutation.mutate(values)
      return
    }
    const used = editing?.used_usd ?? 0
    const alreadyOver = editing !== null && used >= values.limit_usd
    Modal.confirm({
      title: '切到硬拦？',
      okText: alreadyOver ? '确认，立即生效' : '确认',
      okButtonProps: { danger: true },
      content: (
        <Space direction="vertical" size={4}>
          <Typography.Text>
            这会让该层设备在超限后无法继续跑 agent（会话被结束）。
          </Typography.Text>
          {alreadyOver && (
            <Typography.Text type="danger">
              该层本周期已用 {money(used)} ≥ 上限 {money(values.limit_usd)}，
              保存后将立即对正在跑的会话生效。
            </Typography.Text>
          )}
        </Space>
      ),
      onOk: () => saveMutation.mutateAsync(values),
    })
  }

  const openDrill = (deviceId: string) => {
    setDrillDeviceId(deviceId)
  }

  const clearDeviceFilter = () => {
    const next = new URLSearchParams(searchParams)
    next.delete('device_id')
    setSearchParams(next, { replace: true })
  }

  const scopeColumns: ColumnsType<UsageByScopeItem> = [
    {
      title: '设备',
      dataIndex: 'device_id',
      ellipsis: true,
      render: (id: string) => (
        <Button
          type="link"
          style={{ padding: 0 }}
          onClick={() => navigate(`/devices?device_id=${encodeURIComponent(id)}`)}
        >
          {id}
        </Button>
      ),
    },
    {
      title: 'org / team',
      key: 'org',
      width: 180,
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
      title: '会话数',
      dataIndex: 'sessions',
      align: 'right',
      width: 90,
      render: (n: number) => (n === 0 ? <Tag color="warning">0</Tag> : n),
    },
    {
      title: 'cost_usd',
      dataIndex: 'cost_usd',
      align: 'right',
      width: 110,
      sorter: (a, b) => a.cost_usd - b.cost_usd,
      render: (v: number) => <Typography.Text strong>{money(v)}</Typography.Text>,
    },
    {
      title: 'side_cost_usd',
      dataIndex: 'side_cost_usd',
      align: 'right',
      width: 130,
      render: optionalMoney,
    },
    {
      title: 'prompt_total',
      dataIndex: 'prompt_total',
      align: 'right',
      width: 120,
      render: (v: number) => formatTokens(v),
    },
    {
      title: 'cache_hit',
      key: 'cache_hit',
      align: 'right',
      width: 140,
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <span>{formatTokens(row.cache_hit)}</span>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            命中 {hitRate(row)}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: 'output',
      dataIndex: 'output',
      align: 'right',
      width: 100,
      render: (v: number) => formatTokens(v),
    },
    {
      title: '最后上报',
      dataIndex: 'last_received_at',
      width: 160,
      render: formatTs,
    },
    {
      title: '下钻',
      key: 'drill',
      width: 90,
      render: (_, row) => (
        <Button size="small" onClick={() => openDrill(row.device_id)}>
          会话
        </Button>
      ),
    },
  ]

  const budgetColumns: ColumnsType<BudgetItem> = [
    {
      title: '层',
      dataIndex: 'scope_type',
      width: 80,
      render: (t: string) => <Tag>{t}</Tag>,
    },
    {
      title: '对象',
      dataIndex: 'scope_id',
      render: (id: string, row) => (
        <Space direction="vertical" size={0}>
          <Typography.Text code>{id}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            org {row.org_id}
          </Typography.Text>
        </Space>
      ),
    },
    { title: '周期', dataIndex: 'period', width: 90 },
    {
      title: '本周期 used / limit',
      key: 'used',
      width: 200,
      render: (_, row) => {
        const over = row.used_usd >= row.limit_usd
        return (
          <Space size={4}>
            <Typography.Text type={over ? 'danger' : undefined} strong={over}>
              {money(row.used_usd)}
            </Typography.Text>
            <Typography.Text type="secondary">/ {money(row.limit_usd)}</Typography.Text>
            {over && <Tag color="error">超限</Tag>}
          </Space>
        )
      },
    },
    {
      title: '超限动作',
      dataIndex: 'enforcement',
      width: 130,
      render: (value: BudgetEnforcement) =>
        value === 'block' ? <Tag color="red">硬拦（结束会话）</Tag> : <Tag color="gold">告警放行</Tag>,
    },
    {
      title: '下发',
      dataIndex: 'disabled',
      width: 90,
      render: (disabled: boolean) => (disabled ? <Tag>已停用</Tag> : <Tag color="green">下发中</Tag>),
    },
    {
      title: '最后修改',
      dataIndex: 'updated_at',
      width: 160,
      render: (value: string, row) => (
        <Space direction="vertical" size={0}>
          <span>{formatTs(value)}</span>
          {row.updated_by && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {row.updated_by}
            </Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 240,
      render: (_, row) => (
        <Space size="small">
          <Button size="small" onClick={() => openEdit(row)}>
            改
          </Button>
          <Button size="small" onClick={() => setToggleTarget({ id: row.id, enabled: row.disabled })}>
            {row.disabled ? '启用' : '停用'}
          </Button>
          <Button size="small" onClick={() => setAuditId(row.id)}>
            审计
          </Button>
          <Button size="small" danger onClick={() => setDeleteTarget(row)}>
            删除
          </Button>
        </Space>
      ),
    },
  ]

  const ledgerColumns: ColumnsType<UsageLedgerItem> = [
    {
      title: 'session_id',
      dataIndex: 'session_id',
      ellipsis: true,
      render: (id: string) =>
        trajectorySessions.has(id) ? (
          <Button type="link" style={{ padding: 0 }} onClick={() => navigate(`/trajectories/${id}`)}>
            {id}
          </Button>
        ) : (
          <Typography.Text code>{id}</Typography.Text>
        ),
    },
    { title: '会话时间', dataIndex: 'ts', width: 150, render: formatEpochSeconds },
    {
      title: '到达',
      dataIndex: 'received_at',
      width: 150,
      render: formatTs,
    },
    {
      title: 'model / provider',
      key: 'model',
      width: 200,
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Typography.Text>{row.model}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {row.provider}
            {row.endpoint_host ? ` · ${row.endpoint_host}` : ''}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: 'cost_usd',
      dataIndex: 'cost_usd',
      align: 'right',
      width: 100,
      render: (v: number) => money(v),
    },
    {
      title: 'side_cost_usd',
      dataIndex: 'side_cost_usd',
      align: 'right',
      width: 120,
      render: optionalMoney,
    },
    {
      title: 'prompt_total',
      dataIndex: 'prompt_total',
      align: 'right',
      width: 110,
      render: (v: number) => formatTokens(v),
    },
    {
      title: 'cache_hit',
      key: 'cache_hit',
      align: 'right',
      width: 110,
      render: (_, row) => `${formatTokens(row.cache_hit)}（${hitRate(row)}）`,
    },
    {
      title: 'output',
      dataIndex: 'output',
      align: 'right',
      width: 90,
      render: (v: number) => formatTokens(v),
    },
    {
      title: 'app_version',
      dataIndex: 'app_version',
      width: 110,
      render: (v: string | null) => v || '—',
    },
  ]

  const auditColumns: ColumnsType<BudgetAuditItem> = [
    { title: '时间', dataIndex: 'created_at', width: 160, render: formatTs },
    {
      title: '对象',
      key: 'scope',
      width: 220,
      render: (_, row) => (
        <Typography.Text code>
          {row.scope_type}:{row.scope_id}
        </Typography.Text>
      ),
    },
    { title: '动作', dataIndex: 'action', width: 90 },
    {
      title: '变更',
      key: 'change',
      ellipsis: true,
      render: (_, row) => (
        <Typography.Text code>
          {JSON.stringify(row.old)} → {JSON.stringify(row.new)}
        </Typography.Text>
      ),
    },
    { title: '原因', dataIndex: 'reason', ellipsis: true },
    { title: '操作人', dataIndex: 'actor', width: 110 },
  ]

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message="cost_usd 含辅助调用，prompt_total 不含 —— 不要相除当单价"
        description={
          <span>
            数字来自设备上报的账本，按 <Typography.Text code>(device, session)</Typography.Text> upsert：
            同一会话多次上报是<Typography.Text strong>覆盖</Typography.Text>不是累加。
            <Typography.Text code>cost_usd</Typography.Text> 含标题 / 子代理 / 分类器等辅助调用，
            <Typography.Text code>prompt_total</Typography.Text> 只算主循环，两者口径不同源。
            命中率的分母是 <Typography.Text code>prompt_total</Typography.Text>，不是请求数。
            轨迹页的「成本」来自另一份数（按轨迹聚合，没有设备归属），对不上时以本页为准。
            远程超限默认告警放行；切硬拦会结束该层设备上的会话。
            不可信渠道（endpoint_host 已标注）仍入库，但不要拿去对官方账单。
          </span>
        }
      />

      <Row gutter={16}>
        <Col span={6}>
          <StatsCard
            title={`本周期成本（${byScope?.period_key ?? '—'}）`}
            value={money(totals.cost)}
            hint="Σ cost_usd，含辅助调用。对账的分子"
          />
        </Col>
        <Col span={6}>
          <StatsCard
            title="本周期会话数"
            value={String(totals.sessions)}
            hint={totals.sessions === 0 ? '0 = 账本通道没接上' : '按 (device, session) 去重后的行数'}
          />
        </Col>
        <Col span={6}>
          <StatsCard
            title="其中辅助调用"
            value={totals.sideSeen ? money(totals.side) : '—'}
            hint="已含在上面的成本里，不要两处相加"
          />
        </Col>
        <Col span={6}>
          <StatsCard
            title="超限的层"
            value={String(overLimitBudgets.length)}
            hint={
              overLimitBudgets.length === 0
                ? '没有 used ≥ limit 的生效预算'
                : `其中 ${blockedOverLimit} 层是硬拦（设备已被停）`
            }
          />
        </Col>
      </Row>

      <Card
        title="按设备看用量"
        extra={
          <Space>
            <Radio.Group
              value={period}
              onChange={e => setPeriod(e.target.value)}
              optionType="button"
              buttonStyle="solid"
              options={PERIODS}
            />
            <Select
              allowClear
              showSearch
              placeholder="组织"
              style={{ width: 180 }}
              value={orgFilter}
              onChange={v => setOrgFilter(v)}
              options={(orgs ?? []).map(o => ({ value: o.org_id, label: `${o.org_id}（${o.name}）` }))}
            />
            <Space size={4}>
              <Switch checked={onlyOverBudget} onChange={setOnlyOverBudget} size="small" />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                只看超预算
              </Typography.Text>
            </Space>
            {deviceFromUrl && (
              <Button size="small" onClick={clearDeviceFilter}>
                取消设备筛选（{deviceFromUrl}）
              </Button>
            )}
          </Space>
        }
      >
        <Table
          rowKey="device_id"
          size="small"
          loading={scopeLoading}
          columns={scopeColumns}
          dataSource={scopeRows}
          pagination={false}
        />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          周期键由服务端计算（{byScope?.period ?? period} / {byScope?.period_key ?? '—'}）。
          「只看超预算」按任一覆盖该设备的层是否超限筛，不排 device &gt; team &gt; org 优先级 ——
          真正的命中层看下方「预览命中」。
        </Typography.Text>
      </Card>

      <Card
        title="预算（超限开关）"
        extra={
          <Space>
            <Button onClick={() => setAuditId('all')}>全部审计</Button>
            <Button type="primary" onClick={openCreate}>
              新建预算
            </Button>
          </Space>
        }
      >
        <Table
          rowKey="id"
          size="small"
          loading={budgetsLoading}
          columns={budgetColumns}
          dataSource={budgets ?? []}
          pagination={false}
          rowClassName={row => (!row.disabled && row.used_usd >= row.limit_usd ? 'cost-row-over' : '')}
        />
        <Space style={{ marginTop: 16 }} wrap>
          <Input
            placeholder="设备 ID，预览会命中哪一层"
            value={previewDeviceId}
            onChange={e => setPreviewDeviceId(e.target.value)}
            style={{ width: 280 }}
          />
          <Button
            onClick={() => {
              const id = previewDeviceId.trim()
              if (!id) {
                message.error('填设备 ID')
                return
              }
              previewMutation.mutate(id)
            }}
            loading={previewMutation.isPending}
          >
            预览命中
          </Button>
          {previewHit === 'empty' && (
            <Typography.Text type="secondary">三层都没有生效预算（客户端按没配远程预算处理）</Typography.Text>
          )}
          {previewHit && previewHit !== 'empty' && (
            <Typography.Text>
              将命中 <Tag>{previewHit.layer}</Tag>
              <Typography.Text code>{previewHit.scope_id}</Typography.Text>
              {' · '}
              {previewHit.period}（{previewHit.period_key}）
              {' · '}
              <Typography.Text type={previewHit.used_usd >= previewHit.limit_usd ? 'danger' : undefined}>
                {money(previewHit.used_usd)} / {money(previewHit.limit_usd)}
              </Typography.Text>
              {' · '}
              {previewHit.enforcement === 'block' ? '硬拦' : '告警放行'}
            </Typography.Text>
          )}
        </Space>
      </Card>

      <Modal
        open={createOpen || editing !== null}
        title={editing ? `修改预算 ${editing.scope_type}:${editing.scope_id}` : '新建预算'}
        onCancel={closeForm}
        onOk={() => form.submit()}
        confirmLoading={saveMutation.isPending}
        destroyOnHidden
        width={560}
      >
        <Form form={form} layout="vertical" onFinish={submitForm}>
          <Form.Item name="scope_type" label="层" rules={[{ required: true }]}>
            <Select
              disabled={editing !== null}
              options={[
                { value: 'org', label: '组织' },
                { value: 'team', label: '团队' },
                { value: 'device', label: '设备' },
              ]}
            />
          </Form.Item>
          <Form.Item name="org_id" label="组织 ID" rules={[{ required: true, message: '必填' }]}>
            <Select
              disabled={editing !== null}
              showSearch
              placeholder="corp-shanghai"
              options={(orgs ?? []).map(o => ({ value: o.org_id, label: `${o.org_id}（${o.name}）` }))}
            />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(prev, cur) => prev.scope_type !== cur.scope_type}>
            {({ getFieldValue }) =>
              getFieldValue('scope_type') === 'org' ? null : (
                <Form.Item
                  name="scope_id"
                  label={getFieldValue('scope_type') === 'team' ? '团队 ID' : '设备 ID'}
                  rules={[{ required: true, message: '必填' }]}
                >
                  <Input
                    disabled={editing !== null}
                    placeholder={getFieldValue('scope_type') === 'team' ? 'infra-platform' : 'dev-1'}
                  />
                </Form.Item>
              )
            }
          </Form.Item>
          <Form.Item
            name="period"
            label="周期"
            rules={[{ required: true }]}
            extra="session 周期在服务端只看该设备最新一行；会话级硬停仍由客户端本地 costLimit 负责。"
          >
            <Select
              options={[
                { value: 'monthly', label: '自然月（UTC）' },
                { value: 'weekly', label: 'ISO 周' },
                { value: 'daily', label: '自然日（UTC）' },
                { value: 'session', label: '单会话' },
              ]}
            />
          </Form.Item>
          <Form.Item name="limit_usd" label="上限（USD）" rules={[{ required: true, message: '必填' }]}>
            <InputNumber min={0.01} step={1} style={{ width: '100%' }} placeholder="500" />
          </Form.Item>
          <Form.Item
            name="enforcement"
            label="超限动作"
            rules={[{ required: true }]}
            extra="没有「降级模型」——客户端 loop 没接这条线，做了也是死开关。"
          >
            <Radio.Group
              options={[
                { value: 'alert', label: '告警放行' },
                { value: 'block', label: '硬拦（结束会话）' },
              ]}
            />
          </Form.Item>
          <Form.Item name="reason" label="变更原因（进审计）" rules={[{ required: true, message: '必填' }]}>
            <Input placeholder="Q4 组织预算收紧到 500；超限先告警观察一周" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        open={toggleTarget !== null}
        title={toggleTarget?.enabled ? '启用预算' : '停用预算'}
        onCancel={() => {
          setToggleTarget(null)
          setActionReason('')
        }}
        onOk={() => {
          if (!toggleTarget) return
          if (!actionReason.trim()) {
            message.error('原因必填')
            return
          }
          toggleMutation.mutate({ ...toggleTarget, reason: actionReason.trim() })
        }}
        confirmLoading={toggleMutation.isPending}
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <Typography.Text type="secondary">
            {toggleTarget?.enabled ? '启用后重新进入下发。' : '停用后不进下发，该层设备按没配远程预算处理。'}
          </Typography.Text>
          <Input
            placeholder="为什么停用 / 启用"
            value={actionReason}
            onChange={e => setActionReason(e.target.value)}
          />
        </Space>
      </Modal>

      <Modal
        open={deleteTarget !== null}
        title="删除预算"
        okButtonProps={{ danger: true }}
        onCancel={() => {
          setDeleteTarget(null)
          setActionReason('')
        }}
        onOk={() => {
          if (!deleteTarget) return
          if (!actionReason.trim()) {
            message.error('原因必填')
            return
          }
          deleteMutation.mutate({ id: deleteTarget.id, reason: actionReason.trim() })
        }}
        confirmLoading={deleteMutation.isPending}
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <Typography.Text type="secondary">审计行保留（记得住删的是哪一层）。账本不受影响。</Typography.Text>
          <Input
            placeholder="为什么删除"
            value={actionReason}
            onChange={e => setActionReason(e.target.value)}
          />
        </Space>
      </Modal>

      <Modal
        open={auditId !== null}
        title={typeof auditId === 'number' ? `预算 #${auditId} 变更记录` : '全部变更记录'}
        footer={null}
        width={960}
        onCancel={() => setAuditId(null)}
      >
        <Table
          rowKey="id"
          size="small"
          loading={auditLoading}
          columns={auditColumns}
          dataSource={audit ?? []}
          pagination={{ pageSize: 10 }}
        />
      </Modal>

      <Modal
        open={drillDeviceId !== null}
        title={`会话明细 · ${drillDeviceId ?? ''}`}
        footer={null}
        width={1200}
        onCancel={() => setDrillDeviceId(null)}
      >
        <Space direction="vertical" size="small" style={{ width: '100%' }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            一行一个会话（upsert 键是 device + session）。到达时间比会话时间晚很多 = 补传。
            session_id 只在轨迹已入库时可点：账本先到、轨迹后到。
          </Typography.Text>
          <Table
            rowKey="id"
            size="small"
            loading={ledgerLoading}
            columns={ledgerColumns}
            dataSource={ledger?.items ?? []}
            pagination={{ pageSize: 20, showTotal: total => `共 ${total} 条` }}
          />
        </Space>
      </Modal>
    </Space>
  )
}
