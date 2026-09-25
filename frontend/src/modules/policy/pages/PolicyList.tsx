import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Collapse,
  Form,
  Input,
  Modal,
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
import { fetchOrganizations } from '../../identity/services/api'
import {
  createPolicy,
  deletePolicy,
  evaluateDevicePolicy,
  fetchPolicyAudit,
  fetchPolicies,
  setPolicyEnabled,
  updatePolicy,
} from '../services/api'
import type { PolicyAuditItem, PolicyEvaluate, PolicyItem, PolicyScopeType, PolicySettings } from '../types/policy'

const LIMIT_FEATURES = [
  { key: 'mcp', label: '禁止 MCP' },
  { key: 'sub_agent', label: '禁止子代理' },
  { key: 'custom_commands', label: '禁止自定义命令' },
  { key: 'extensions', label: '禁止扩展/技能' },
] as const

const PERMISSION_MODES = [
  'default',
  'manual',
  'always-allow',
  'deny-write',
  'acceptEdits',
  'plan',
  'dontAsk',
  'auto',
  'dangerously-skip-permissions',
]

const SURFACES = ['commands', 'skills', 'agents', 'hooks', 'mcp-servers']

function formatTs(value: string | null | undefined) {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '—'
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

function splitLines(raw: string | undefined): string[] {
  return (raw ?? '')
    .split('\n')
    .map(s => s.trim())
    .filter(Boolean)
}

function settingsSummary(settings: PolicySettings): string {
  const bits: string[] = []
  const deny = settings.permissions?.deny?.length ?? 0
  const allow = settings.permissions?.allow?.length ?? 0
  const ask = settings.permissions?.ask?.length ?? 0
  if (deny) bits.push(`deny×${deny}`)
  if (allow) bits.push(`allow×${allow}`)
  if (ask) bits.push(`ask×${ask}`)
  const closed = Object.entries(settings.policyLimits ?? {})
    .filter(([, v]) => v.allowed === false)
    .map(([k]) => k)
  if (closed.length) bits.push(`关 ${closed.join('/')}`)
  if (settings.disableAllHooks) bits.push('禁全部 Hook')
  if (settings.allowManagedHooksOnly) bits.push('仅托管 Hook')
  if (settings.disableBypassPermissionsMode === 'disable') bits.push('禁 bypass')
  if (settings.disabledModes?.length) bits.push(`模式 ${settings.disabledModes.join('/')}`)
  if (settings.strictPluginOnlyCustomization) bits.push('锁定定制化')
  if (settings.bridgeEnabled === false) bits.push('禁止遥控')
  return bits.join(' · ') || '（无摘要）'
}

type FormValues = {
  scope_type: PolicyScopeType
  org_id: string
  scope_id: string
  deny?: string
  allow?: string
  ask?: string
  limits?: string[]
  limitReasons?: Record<string, string>
  disableAllHooks?: boolean
  allowManagedHooksOnly?: boolean
  allowManagedPermissionRulesOnly?: boolean
  disableBypass?: boolean
  disabledModes?: string[]
  strictAll?: boolean
  strictSurfaces?: string[]
  bridgeDisabled?: boolean
  reason: string
}

function toSettings(values: FormValues): PolicySettings {
  const settings: PolicySettings = {}
  const deny = splitLines(values.deny)
  const allow = splitLines(values.allow)
  const ask = splitLines(values.ask)
  if (deny.length || allow.length || ask.length) {
    settings.permissions = {
      ...(deny.length ? { deny } : {}),
      ...(allow.length ? { allow } : {}),
      ...(ask.length ? { ask } : {}),
    }
  }
  if (values.limits?.length) {
    const policyLimits: NonNullable<PolicySettings['policyLimits']> = {}
    for (const key of values.limits) {
      policyLimits[key] = {
        allowed: false,
        reason: (values.limitReasons?.[key] ?? '').trim(),
      }
    }
    settings.policyLimits = policyLimits
  }
  if (values.disableAllHooks) settings.disableAllHooks = true
  if (values.allowManagedHooksOnly) settings.allowManagedHooksOnly = true
  if (values.allowManagedPermissionRulesOnly) settings.allowManagedPermissionRulesOnly = true
  if (values.disableBypass) settings.disableBypassPermissionsMode = 'disable'
  if (values.disabledModes?.length) settings.disabledModes = values.disabledModes
  if (values.strictAll) settings.strictPluginOnlyCustomization = true
  else if (values.strictSurfaces?.length) settings.strictPluginOnlyCustomization = values.strictSurfaces
  if (values.bridgeDisabled) settings.bridgeEnabled = false
  return settings
}

function fromItem(row: PolicyItem): FormValues {
  const limits = Object.entries(row.settings.policyLimits ?? {})
    .filter(([, v]) => v.allowed === false)
    .map(([k]) => k)
  const limitReasons: Record<string, string> = {}
  for (const [k, v] of Object.entries(row.settings.policyLimits ?? {})) {
    if (v.reason) limitReasons[k] = v.reason
  }
  const strict = row.settings.strictPluginOnlyCustomization
  return {
    scope_type: row.scope_type,
    org_id: row.org_id,
    scope_id: row.scope_id,
    deny: (row.settings.permissions?.deny ?? []).join('\n'),
    allow: (row.settings.permissions?.allow ?? []).join('\n'),
    ask: (row.settings.permissions?.ask ?? []).join('\n'),
    limits,
    limitReasons,
    disableAllHooks: !!row.settings.disableAllHooks,
    allowManagedHooksOnly: !!row.settings.allowManagedHooksOnly,
    allowManagedPermissionRulesOnly: !!row.settings.allowManagedPermissionRulesOnly,
    disableBypass: row.settings.disableBypassPermissionsMode === 'disable',
    disabledModes: row.settings.disabledModes ?? [],
    strictAll: strict === true,
    strictSurfaces: Array.isArray(strict) ? strict : [],
    bridgeDisabled: row.settings.bridgeEnabled === false,
    reason: '',
  }
}

export default function PolicyList() {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<PolicyItem | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [auditId, setAuditId] = useState<number | null | 'all'>(null)
  const [scopeFilter, setScopeFilter] = useState<PolicyScopeType | undefined>()
  const [orgFilter, setOrgFilter] = useState<string | undefined>()
  const [toggleReason, setToggleReason] = useState<{ id: number; enabled: boolean } | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<PolicyItem | null>(null)
  const [actionReason, setActionReason] = useState('')
  const [form] = Form.useForm<FormValues>()
  const [previewDeviceId, setPreviewDeviceId] = useState('')
  const [previewHit, setPreviewHit] = useState<PolicyEvaluate | null | 'empty'>(null)

  const { data: orgs } = useQuery({
    queryKey: ['identity-organizations'],
    queryFn: fetchOrganizations,
  })

  const listParams = useMemo(() => {
    const params: Record<string, string> = {}
    if (scopeFilter) params.scope_type = scopeFilter
    if (orgFilter) params.org_id = orgFilter
    return params
  }, [scopeFilter, orgFilter])

  const { data: policies, isLoading } = useQuery({
    queryKey: ['policies', listParams],
    queryFn: () => fetchPolicies(listParams),
  })

  const { data: audit, isLoading: auditLoading } = useQuery({
    queryKey: ['policy-audit', auditId],
    queryFn: () => fetchPolicyAudit(typeof auditId === 'number' ? auditId : undefined),
    enabled: auditId !== null,
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['policies'] })

  const closeModal = () => {
    setCreateOpen(false)
    setEditing(null)
    form.resetFields()
  }

  const saveMutation = useMutation({
    mutationFn: async (values: FormValues) => {
      const settings = toSettings(values)
      const payload = { settings, reason: values.reason.trim() }
      if (!payload.reason) throw new Error('变更原因必填')
      return editing
        ? updatePolicy(editing.id, payload)
        : createPolicy({
            scope_type: values.scope_type,
            scope_id: values.scope_type === 'org' ? values.org_id.trim() : values.scope_id.trim(),
            org_id: values.org_id.trim(),
            ...payload,
          })
    },
    onSuccess: async () => {
      message.success(editing ? '已更新' : '已创建')
      closeModal()
      await invalidate()
    },
    onError: (err: Error) => message.error(errorText(err, err.message || '保存失败')),
  })

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled, reason }: { id: number; enabled: boolean; reason: string }) =>
      setPolicyEnabled(id, enabled, reason),
    onSuccess: async (_data, vars) => {
      message.success(vars.enabled ? '已启用' : '已停用，不再进入下发')
      setToggleReason(null)
      setActionReason('')
      await invalidate()
    },
    onError: err => message.error(errorText(err, '操作失败')),
  })

  const deleteMutation = useMutation({
    mutationFn: ({ id, reason }: { id: number; reason: string }) => deletePolicy(id, reason),
    onSuccess: async () => {
      message.success('已删除，审计记录保留')
      setDeleteTarget(null)
      setActionReason('')
      await invalidate()
    },
    onError: err => message.error(errorText(err, '删除失败')),
  })

  const openCreate = () => {
    form.resetFields()
    form.setFieldsValue({ scope_type: 'org', limits: [], disabledModes: [], strictSurfaces: [] })
    setEditing(null)
    setCreateOpen(true)
  }

  const openEdit = (row: PolicyItem) => {
    setEditing(row)
    form.setFieldsValue(fromItem(row))
  }

  const previewMutation = useMutation({
    mutationFn: (deviceId: string) => evaluateDevicePolicy(deviceId),
    onSuccess: data => setPreviewHit(data ?? 'empty'),
    onError: err => message.error(errorText(err, '预览失败')),
  })

  const preview = Form.useWatch([], form)
  const previewJson = useMemo(() => {
    try {
      return JSON.stringify(toSettings((preview ?? {}) as FormValues), null, 2)
    } catch {
      return '{}'
    }
  }, [preview])

  const columns: ColumnsType<PolicyItem> = [
    {
      title: '层',
      dataIndex: 'scope_type',
      width: 80,
      render: (t: PolicyScopeType) => <Tag>{t}</Tag>,
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
    {
      title: '约束',
      key: 'summary',
      ellipsis: true,
      render: (_, row) => settingsSummary(row.settings),
    },
    {
      title: '下发',
      dataIndex: 'disabled',
      width: 90,
      render: (disabled: boolean) =>
        disabled ? <Tag>已停用</Tag> : <Tag color="green">下发中</Tag>,
    },
    {
      title: '最后修改',
      dataIndex: 'updated_at',
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
      width: 260,
      render: (_, row) => (
        <Space size="small">
          <Button size="small" onClick={() => openEdit(row)}>
            改
          </Button>
          <Button size="small" onClick={() => setToggleReason({ id: row.id, enabled: row.disabled })}>
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

  const auditColumns: ColumnsType<PolicyAuditItem> = [
    { title: '时间', dataIndex: 'created_at', width: 150, render: formatTs },
    {
      title: '对象',
      key: 'scope',
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
          {JSON.stringify(row.old_settings)} → {JSON.stringify(row.new_settings)}
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
        message="策略按 device > team > org 生效，不合并"
        description={
          <span>
            下发 <Typography.Text code>GET /traj/api/v1/ctl/policy</Typography.Text> 要设备凭据，不是无认证。
            设备有一份就看不到组织那份。空策略不允许（会盖掉员工本地 managed）。
            policyLimits 只有 mcp / sub_agent / custom_commands / extensions 真拦。
            「变更原因」进审计；用户看到的是各功能的禁用理由。按设备 ID 筛：层选设备，对象填 device_id。
          </span>
        }
      />

      <Card
        title="策略"
        extra={
          <Space>
            <Select
              allowClear
              placeholder="层"
              style={{ width: 120 }}
              value={scopeFilter}
              onChange={v => setScopeFilter(v)}
              options={[
                { value: 'org', label: '组织' },
                { value: 'team', label: '团队' },
                { value: 'device', label: '设备' },
              ]}
            />
            <Select
              allowClear
              showSearch
              placeholder="组织"
              style={{ width: 180 }}
              value={orgFilter}
              onChange={v => setOrgFilter(v)}
              options={(orgs ?? []).map(o => ({ value: o.org_id, label: o.org_id }))}
            />
            <Button onClick={() => setAuditId('all')}>全部审计</Button>
            <Button type="primary" onClick={openCreate}>
              新建策略
            </Button>
          </Space>
        }
      >
        <Table
          rowKey="id"
          loading={isLoading}
          columns={columns}
          dataSource={policies ?? []}
          pagination={false}
        />
        <Space style={{ marginTop: 16 }}>
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
          {previewHit === 'empty' && <Typography.Text type="secondary">三层都没有生效策略</Typography.Text>}
          {previewHit && previewHit !== 'empty' && (
            <Typography.Text>
              将命中 <Tag>{previewHit.layer}</Tag>
              <Typography.Text code>{previewHit.scope_id}</Typography.Text>
              {' · '}
              {settingsSummary(previewHit.settings)}
            </Typography.Text>
          )}
        </Space>
      </Card>

      <Modal
        open={createOpen || editing !== null}
        title={editing ? `修改 ${editing.scope_type}:${editing.scope_id}` : '新建策略'}
        onCancel={closeModal}
        onOk={() => form.submit()}
        confirmLoading={saveMutation.isPending}
        destroyOnHidden
        width={720}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={values => saveMutation.mutate(values)}
          initialValues={{ scope_type: 'org' }}
        >
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
                  <Input disabled={editing !== null} placeholder={getFieldValue('scope_type') === 'team' ? 'infra' : 'dev-1'} />
                </Form.Item>
              )
            }
          </Form.Item>
          <Form.Item
            name="deny"
            label="permissions.deny"
            extra="一行一条，例如 Bash(curl *)。远程 deny 会进 checker。"
          >
            <Input.TextArea rows={3} placeholder={'Bash(curl *)'} />
          </Form.Item>
          <Collapse
            items={[
              {
                key: 'advanced-perms',
                label: '高级：allow / ask',
                children: (
                  <>
                    <Form.Item name="allow" label="permissions.allow">
                      <Input.TextArea rows={2} />
                    </Form.Item>
                    <Form.Item name="ask" label="permissions.ask">
                      <Input.TextArea rows={2} />
                    </Form.Item>
                  </>
                ),
              },
            ]}
          />
          <Form.Item name="limits" label="关掉的功能（policyLimits）">
            <Checkbox.Group
              options={LIMIT_FEATURES.map(f => ({ label: f.label, value: f.key }))}
            />
          </Form.Item>
          <Form.Item noStyle shouldUpdate>
            {({ getFieldValue }) =>
              (getFieldValue('limits') as string[] | undefined)?.map(key => (
                <Form.Item
                  key={key}
                  name={['limitReasons', key]}
                  label={`${key} 禁用理由（用户可见）`}
                  rules={[{ required: true, message: 'allowed=false 时必填' }]}
                >
                  <Input placeholder="未审计的 MCP 不允许" />
                </Form.Item>
              ))
            }
          </Form.Item>
          <Form.Item name="disableAllHooks" label="禁用全部 Hook" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="allowManagedHooksOnly" label="只允许托管 Hook" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item
            name="allowManagedPermissionRulesOnly"
            label="只允许策略里的权限规则"
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
          <Form.Item name="disableBypass" label="禁用 bypass 权限模式" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="disabledModes" label="禁用的权限模式">
            <Select mode="tags" options={PERMISSION_MODES.map(m => ({ value: m, label: m }))} />
          </Form.Item>
          <Form.Item
            name="bridgeDisabled"
            label="禁止 Bridge 远程控制"
            valuePropName="checked"
            extra="关闭后该范围的客户端不再接受遥控。省略等于不关。"
          >
            <Switch />
          </Form.Item>
          <Form.Item name="strictAll" label="锁定全部定制化面" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item noStyle shouldUpdate>
            {({ getFieldValue }) =>
              getFieldValue('strictAll') ? null : (
                <Form.Item name="strictSurfaces" label="锁定部分定制化面">
                  <Select mode="multiple" options={SURFACES.map(s => ({ value: s, label: s }))} />
                </Form.Item>
              )
            }
          </Form.Item>
          <Form.Item
            name="reason"
            label="变更原因（进审计，不是用户看到的禁用理由）"
            rules={[{ required: true, message: '必填' }]}
          >
            <Input placeholder="生产网禁 curl 外带；MCP 待白名单" />
          </Form.Item>
          <Collapse
            items={[
              {
                key: 'preview',
                label: '即将下发的 settings（不含 source）',
                children: <pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{previewJson}</pre>,
              },
            ]}
          />
        </Form>
      </Modal>

      <Modal
        open={toggleReason !== null}
        title={toggleReason?.enabled ? '启用策略' : '停用策略'}
        onCancel={() => {
          setToggleReason(null)
          setActionReason('')
        }}
        onOk={() => {
          if (!toggleReason) return
          if (!actionReason.trim()) {
            message.error('原因必填')
            return
          }
          toggleMutation.mutate({ ...toggleReason, reason: actionReason.trim() })
        }}
        confirmLoading={toggleMutation.isPending}
      >
        <Input
          placeholder="为什么停用 / 启用"
          value={actionReason}
          onChange={e => setActionReason(e.target.value)}
        />
      </Modal>

      <Modal
        open={deleteTarget !== null}
        title="删除策略"
        onCancel={() => {
          setDeleteTarget(null)
          setActionReason('')
        }}
        okButtonProps={{ danger: true }}
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
        <Input
          placeholder="为什么删除"
          value={actionReason}
          onChange={e => setActionReason(e.target.value)}
        />
      </Modal>

      <Modal
        open={auditId !== null}
        title={typeof auditId === 'number' ? `策略 #${auditId} 变更记录` : '全部变更记录'}
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
    </Space>
  )
}
