import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Card, Form, Input, Modal, Select, Space, Table, Tag, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import {
  createOrganization,
  createTeam,
  fetchIdentityAudit,
  fetchOrganizations,
  fetchTeams,
  renameOrganization,
  renameTeam,
} from '../services/api'
import type { IdentityAuditItem, OrganizationItem, TeamItem } from '../types/identity'

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

const ACTION_LABEL: Record<string, string> = {
  org_create: '新建组织',
  org_rename: '组织改名',
  team_create: '新建团队',
  team_rename: '团队改名',
  device_move: '调整设备归属',
}

// 弹窗的几种用途：新建组织 / 新建团队 / 组织改名 / 团队改名
type Editing =
  | { kind: 'org-create' }
  | { kind: 'team-create'; orgId?: string }
  | { kind: 'org-rename'; org: OrganizationItem }
  | { kind: 'team-rename'; team: TeamItem }

const SLUG_RULE = {
  pattern: /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/,
  message: '字母或数字开头，只能含字母、数字、. _ -',
}

export default function OrgTeamList() {
  const queryClient = useQueryClient()
  const [orgFilter, setOrgFilter] = useState<string | undefined>()
  const [editing, setEditing] = useState<Editing | null>(null)
  const [form] = Form.useForm()

  const { data: orgs, isLoading: orgsLoading } = useQuery({
    queryKey: ['identity-orgs'],
    queryFn: fetchOrganizations,
  })
  const { data: teams, isLoading: teamsLoading } = useQuery({
    queryKey: ['identity-teams', orgFilter],
    queryFn: () => fetchTeams(orgFilter),
  })
  const { data: audit, isLoading: auditLoading } = useQuery({
    queryKey: ['identity-audit'],
    queryFn: () => fetchIdentityAudit({ limit: 100 }),
  })

  const save = useMutation({
    mutationFn: async (values: Record<string, string>) => {
      if (!editing) return
      switch (editing.kind) {
        case 'org-create':
          return createOrganization({ org_id: values.org_id, name: values.name, reason: values.reason })
        case 'team-create':
          return createTeam({
            org_id: values.org_id,
            team_id: values.team_id,
            name: values.name,
            reason: values.reason,
          })
        case 'org-rename':
          return renameOrganization(editing.org.org_id, { name: values.name, reason: values.reason })
        case 'team-rename':
          return renameTeam(editing.team.org_id, editing.team.team_id, {
            name: values.name,
            reason: values.reason,
          })
      }
    },
    onSuccess: async () => {
      message.success('已保存')
      setEditing(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['identity-orgs'] }),
        queryClient.invalidateQueries({ queryKey: ['identity-teams'] }),
        queryClient.invalidateQueries({ queryKey: ['identity-audit'] }),
      ])
    },
    onError: err => message.error(errorText(err, '保存失败')),
  })

  const open = (next: Editing) => {
    form.resetFields()
    if (next.kind === 'org-rename') form.setFieldsValue({ name: next.org.name })
    if (next.kind === 'team-rename') form.setFieldsValue({ name: next.team.name })
    if (next.kind === 'team-create') form.setFieldsValue({ org_id: next.orgId })
    setEditing(next)
  }

  const orgColumns: ColumnsType<OrganizationItem> = [
    { title: '组织 ID', dataIndex: 'org_id', width: 180 },
    { title: '名称', dataIndex: 'name' },
    { title: '设备数', dataIndex: 'device_count', width: 90 },
    { title: '创建', dataIndex: 'created_at', width: 160, render: formatTs },
    {
      title: '操作',
      key: 'action',
      width: 180,
      render: (_v, record) => (
        <Space size="small">
          <Button type="link" style={{ padding: 0 }} onClick={() => open({ kind: 'org-rename', org: record })}>
            改名
          </Button>
          <Button
            type="link"
            style={{ padding: 0 }}
            onClick={() => open({ kind: 'team-create', orgId: record.org_id })}
          >
            新建团队
          </Button>
        </Space>
      ),
    },
  ]

  const teamColumns: ColumnsType<TeamItem> = [
    { title: '组织', dataIndex: 'org_id', width: 180 },
    { title: '团队 ID', dataIndex: 'team_id', width: 180 },
    { title: '名称', dataIndex: 'name' },
    { title: '设备数', dataIndex: 'device_count', width: 90 },
    { title: '创建', dataIndex: 'created_at', width: 160, render: formatTs },
    {
      title: '操作',
      key: 'action',
      width: 90,
      render: (_v, record) => (
        <Button type="link" style={{ padding: 0 }} onClick={() => open({ kind: 'team-rename', team: record })}>
          改名
        </Button>
      ),
    },
  ]

  const auditColumns: ColumnsType<IdentityAuditItem> = [
    { title: '时间', dataIndex: 'created_at', width: 160, render: formatTs },
    {
      title: '动作',
      dataIndex: 'action',
      width: 120,
      render: (v: string) => <Tag>{ACTION_LABEL[v] ?? v}</Tag>,
    },
    {
      title: '对象',
      key: 'target',
      width: 240,
      ellipsis: true,
      render: (_v, r) => (r.target_type === 'org' ? r.target_id : `${r.org_id} / ${r.target_id}`),
    },
    {
      title: '变更',
      dataIndex: 'detail',
      ellipsis: true,
      render: (_v, r) => describeDetail(r),
    },
    { title: '理由', dataIndex: 'reason', ellipsis: true, render: (v: string) => v || '—' },
    { title: '操作者', dataIndex: 'actor', width: 140, ellipsis: true },
  ]

  const isCreate = editing?.kind === 'org-create' || editing?.kind === 'team-create'
  const modalTitle = {
    'org-create': '新建组织',
    'team-create': '新建团队',
    'org-rename': '组织改名',
    'team-rename': '团队改名',
  }[editing?.kind ?? 'org-create']

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <Typography.Title level={4} style={{ color: '#fff', margin: 0 }}>
        组织与团队
      </Typography.Title>
      <Typography.Paragraph style={{ color: '#8c8c8c', marginTop: -8 }}>
        策略与预算按 设备 &gt; 团队 &gt; 组织 命中。飞书登录的新设备默认落在登录组织、不在任何团队，
        到「设备」页用「调整归属」把它分进团队。ID 会被策略、预算、市场条目引用，建好后只能改名称。
      </Typography.Paragraph>

      <Card
        size="small"
        title="组织"
        extra={
          <Button type="primary" onClick={() => open({ kind: 'org-create' })}>
            新建组织
          </Button>
        }
      >
        <Table
          rowKey="org_id"
          columns={orgColumns}
          dataSource={orgs || []}
          loading={orgsLoading}
          pagination={false}
          size="small"
        />
      </Card>

      <Card
        size="small"
        title="团队"
        extra={
          <Space>
            <Select
              allowClear
              placeholder="按组织筛选"
              style={{ width: 200 }}
              value={orgFilter}
              onChange={setOrgFilter}
              options={(orgs || []).map(o => ({ value: o.org_id, label: o.org_id }))}
            />
            <Button type="primary" onClick={() => open({ kind: 'team-create', orgId: orgFilter })}>
              新建团队
            </Button>
          </Space>
        }
      >
        <Table
          rowKey={r => `${r.org_id}/${r.team_id}`}
          columns={teamColumns}
          dataSource={teams || []}
          loading={teamsLoading}
          pagination={false}
          size="small"
        />
      </Card>

      <Card size="small" title="变更审计（最近 100 条）">
        <Table
          rowKey="id"
          columns={auditColumns}
          dataSource={audit || []}
          loading={auditLoading}
          pagination={{ pageSize: 20 }}
          size="small"
        />
      </Card>

      <Modal
        title={modalTitle}
        open={editing !== null}
        onCancel={() => setEditing(null)}
        onOk={async () => {
          const values = await form.validateFields()
          await save.mutateAsync(values)
        }}
        confirmLoading={save.isPending}
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          {editing?.kind === 'org-create' && (
            <Form.Item name="org_id" label="组织 ID" rules={[{ required: true, message: '必填' }, SLUG_RULE]}>
              <Input placeholder="corp-shanghai" />
            </Form.Item>
          )}
          {editing?.kind === 'team-create' && (
            <>
              <Form.Item name="org_id" label="所属组织" rules={[{ required: true, message: '必选' }]}>
                <Select
                  placeholder="选择组织"
                  options={(orgs || []).map(o => ({ value: o.org_id, label: `${o.org_id}（${o.name}）` }))}
                />
              </Form.Item>
              <Form.Item name="team_id" label="团队 ID" rules={[{ required: true, message: '必填' }, SLUG_RULE]}>
                <Input placeholder="infra-platform" />
              </Form.Item>
            </>
          )}
          {editing?.kind === 'org-rename' && (
            <Typography.Paragraph type="secondary">组织 ID：{editing.org.org_id}</Typography.Paragraph>
          )}
          {editing?.kind === 'team-rename' && (
            <Typography.Paragraph type="secondary">
              团队：{editing.team.org_id} / {editing.team.team_id}
            </Typography.Paragraph>
          )}
          <Form.Item
            name="name"
            label="名称"
            rules={isCreate ? [] : [{ required: true, message: '必填' }]}
          >
            <Input placeholder={isCreate ? '可选，默认同 ID' : ''} />
          </Form.Item>
          <Form.Item name="reason" label="理由" rules={[{ required: true, whitespace: true, message: '必填' }]}>
            <Input.TextArea rows={2} placeholder="写进审计" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

function describeDetail(r: IdentityAuditItem): string {
  const d = r.detail as {
    name?: string
    old_name?: string
    new_name?: string
    from?: { org_id?: string; team_id?: string }
    to?: { org_id?: string; team_id?: string }
  }
  const slot = (s?: { org_id?: string; team_id?: string }) =>
    s ? `${s.org_id || '—'} / ${s.team_id || '无团队'}` : '—'
  if (d.from || d.to) return `${slot(d.from)} → ${slot(d.to)}`
  if (d.old_name !== undefined) return `${d.old_name} → ${d.new_name}`
  if (d.name) return `名称：${d.name}`
  return '—'
}
