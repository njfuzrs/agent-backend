import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { InboxOutlined, PlusOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { fetchOrganizations } from '../../identity/services/api'
import { fetchUsers } from '../../auth/services/api'
import {
  createMarketItem,
  fetchMarketAudit,
  fetchMarketDownloads,
  fetchMarketDownloadStats,
  fetchMarketItems,
  setVersionStatus,
  updateMarketItem,
  uploadMarketVersion,
} from '../services/api'
import type {
  MarketAuditItem,
  MarketComponents,
  MarketDownloadItem,
  MarketDownloadStatsItem,
  MarketItem,
  MarketKind,
  MarketVersionItem,
  VersionStatus,
} from '../types/marketplace'

// 后端 marketplace/service/package.py 的上限，超了服务端 413
const MAX_PACKAGE_MB = 20

const KIND_LABELS: Record<MarketKind, string> = { plugin: '插件', skill: '技能', mcp: 'MCP' }

const STATUS_TAGS: Record<VersionStatus, { color: string; label: string }> = {
  draft: { color: 'default', label: '草稿' },
  published: { color: 'green', label: '已发布' },
  yanked: { color: 'red', label: '已下架' },
}

const ACTION_LABELS: Record<MarketAuditItem['action'], string> = {
  create: '登记',
  update: '改元数据',
  upload: '上传',
  publish: '发布',
  yank: '下架',
}

function formatTs(value: string | null | undefined) {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '—'
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

// 包校验失败时后端 detail = { message, errors[] }，其余是字符串或 Pydantic 数组
function errorText(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && 'errors' in detail) {
    const errors = (detail as { errors?: string[] }).errors ?? []
    return errors.length ? errors.join('；') : fallback
  }
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: string }
    if (first?.msg) return first.msg
  }
  return (err as Error)?.message || fallback
}

function scopeText(item: Pick<MarketItem, 'org_id' | 'team_id'>) {
  return item.team_id ? `${item.org_id} / ${item.team_id}` : `${item.org_id}（全组织）`
}

/** 组件清单。hooks 会在员工机器上执行命令、MCP 会连外部地址，单独标出来给上架的人看。 */
function ComponentsView({ components }: { components: MarketComponents }) {
  const list = (items: string[]) =>
    items.length ? items.map(name => <Tag key={name}>{name}</Tag>) : <Typography.Text type="secondary">—</Typography.Text>
  return (
    <Descriptions size="small" column={1} bordered>
      <Descriptions.Item label="Skills">{list(components.skills)}</Descriptions.Item>
      <Descriptions.Item label="Commands">{list(components.commands)}</Descriptions.Item>
      <Descriptions.Item label="Agents">{list(components.agents)}</Descriptions.Item>
      <Descriptions.Item label="Hooks">
        {components.hooks.length ? (
          components.hooks.map(name => (
            <Tag key={name} color="orange">
              {name}
            </Tag>
          ))
        ) : (
          <Typography.Text type="secondary">—</Typography.Text>
        )}
      </Descriptions.Item>
      <Descriptions.Item label="MCP">
        {components.mcpServers.length ? (
          <Space orientation="vertical" size={2}>
            {components.mcpServers.map(s => (
              <Typography.Text key={s.name} code>
                {s.name} · {s.type}
                {s.url ? ` · ${s.url}` : ''}
                {s.command ? ` · ${s.command}` : ''}
                {s.auth ? ` · auth=${s.auth}` : ''}
              </Typography.Text>
            ))}
          </Space>
        ) : (
          <Typography.Text type="secondary">—</Typography.Text>
        )}
      </Descriptions.Item>
    </Descriptions>
  )
}

type ItemFormValues = {
  name: string
  kind: MarketKind
  description?: string
  maintainer?: string
  org_id: string
  team_id?: string
}

type StatusTarget = { item: string; version: string; action: 'publish' | 'yank' }

export default function MarketList() {
  const queryClient = useQueryClient()
  const [itemForm] = Form.useForm<ItemFormValues>()
  const [itemModal, setItemModal] = useState<{ mode: 'create' } | { mode: 'edit'; item: MarketItem } | null>(null)
  const [detailName, setDetailName] = useState<string | null>(null)
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [statusTarget, setStatusTarget] = useState<StatusTarget | null>(null)
  const [reason, setReason] = useState('')
  const [auditName, setAuditName] = useState<string | undefined>()

  const { data: items, isLoading } = useQuery({ queryKey: ['market-items'], queryFn: fetchMarketItems })
  const { data: orgs } = useQuery({ queryKey: ['identity-organizations'], queryFn: fetchOrganizations })
  const { data: users } = useQuery({ queryKey: ['auth-users'], queryFn: fetchUsers })
  const { data: audit, isLoading: auditLoading } = useQuery({
    queryKey: ['market-audit', auditName],
    queryFn: () => fetchMarketAudit(auditName),
  })
  const { data: stats, isLoading: statsLoading } = useQuery({
    queryKey: ['market-download-stats'],
    queryFn: () => fetchMarketDownloadStats(30),
  })
  const { data: downloads, isLoading: downloadsLoading } = useQuery({
    queryKey: ['market-downloads', detailName],
    queryFn: () => fetchMarketDownloads(detailName ?? undefined),
    enabled: detailName !== null,
  })

  const detail = useMemo(() => items?.find(i => i.name === detailName) ?? null, [items, detailName])
  const userName = (id: number | null) => {
    if (id === null) return <Typography.Text type="secondary">未登录设备</Typography.Text>
    const user = users?.find(u => u.id === id)
    return user ? user.name || user.email || `#${id}` : `#${id}`
  }

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['market-items'] })
    queryClient.invalidateQueries({ queryKey: ['market-audit'] })
  }

  const closeItemModal = () => {
    setItemModal(null)
    itemForm.resetFields()
  }

  const itemMutation = useMutation({
    mutationFn: async (values: ItemFormValues) => {
      const payload = {
        kind: values.kind,
        description: (values.description ?? '').trim(),
        maintainer: (values.maintainer ?? '').trim(),
        org_id: values.org_id,
        team_id: (values.team_id ?? '').trim(),
      }
      if (itemModal?.mode === 'edit') return updateMarketItem(itemModal.item.name, payload)
      return createMarketItem({ name: values.name.trim(), ...payload })
    },
    onSuccess: () => {
      message.success(itemModal?.mode === 'edit' ? '已保存' : '已登记，接下来上传插件包')
      closeItemModal()
      invalidate()
    },
    onError: err => message.error(errorText(err, '保存失败')),
  })

  const uploadMutation = useMutation({
    mutationFn: ({ name, file }: { name: string; file: File }) => uploadMarketVersion(name, file),
    onSuccess: v => {
      message.success(`已上传 ${v.version}（草稿），确认组件清单后再发布`)
      setUploadFile(null)
      invalidate()
    },
    onError: err => message.error(errorText(err, '上传失败'), 8),
  })

  const statusMutation = useMutation({
    mutationFn: (t: StatusTarget & { reason: string }) => setVersionStatus(t.item, t.version, t.action, t.reason),
    onSuccess: v => {
      message.success(v.status === 'published' ? `${v.version} 已发布` : `${v.version} 已下架`)
      setStatusTarget(null)
      setReason('')
      invalidate()
    },
    onError: err => message.error(errorText(err, '操作失败')),
  })

  const openCreate = () => {
    itemForm.resetFields()
    itemForm.setFieldsValue({ kind: 'plugin', org_id: orgs?.[0]?.org_id })
    setItemModal({ mode: 'create' })
  }

  const openEdit = (item: MarketItem) => {
    itemForm.setFieldsValue({
      name: item.name,
      kind: item.kind,
      description: item.description,
      maintainer: item.maintainer,
      org_id: item.org_id,
      team_id: item.team_id,
    })
    setItemModal({ mode: 'edit', item })
  }

  const columns: ColumnsType<MarketItem> = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (name: string) => (
        <Button type="link" style={{ padding: 0 }} onClick={() => setDetailName(name)}>
          {name}
        </Button>
      ),
    },
    { title: '类型', dataIndex: 'kind', render: (k: MarketKind) => <Tag>{KIND_LABELS[k] ?? k}</Tag> },
    { title: '描述', dataIndex: 'description', ellipsis: true, render: (v: string) => v || '—' },
    { title: '可见范围', key: 'scope', render: (_v, r) => scopeText(r) },
    {
      title: '最新发布',
      dataIndex: 'latest_version',
      render: (v: string | null) => (v ? <Tag color="green">{v}</Tag> : <Typography.Text type="secondary">未发布</Typography.Text>),
    },
    {
      title: '草稿',
      key: 'drafts',
      render: (_v, r) => r.versions.filter(v => v.status === 'draft').length || '—',
    },
    { title: '下载', dataIndex: 'download_count' },
    { title: '维护人', dataIndex: 'maintainer', render: (v: string) => v || '—' },
    { title: '更新时间', dataIndex: 'updated_at', render: formatTs },
    {
      title: '操作',
      key: 'actions',
      render: (_v, r) => (
        <Space>
          <Button size="small" onClick={() => setDetailName(r.name)}>
            版本
          </Button>
          <Button size="small" onClick={() => openEdit(r)}>
            编辑
          </Button>
          <Button size="small" onClick={() => setAuditName(r.name)}>
            审计
          </Button>
        </Space>
      ),
    },
  ]

  const versionColumns: ColumnsType<MarketVersionItem> = [
    { title: '版本', dataIndex: 'version', render: (v: string) => <Typography.Text strong>{v}</Typography.Text> },
    {
      title: '状态',
      dataIndex: 'status',
      render: (s: VersionStatus) => <Tag color={STATUS_TAGS[s].color}>{STATUS_TAGS[s].label}</Tag>,
    },
    {
      title: 'sha256',
      dataIndex: 'sha256',
      render: (v: string) => (
        <Typography.Text code copyable={{ text: v }}>
          {v.slice(0, 12)}…
        </Typography.Text>
      ),
    },
    { title: '大小', dataIndex: 'size_bytes', render: formatSize },
    { title: '上传', key: 'created', render: (_v, r) => `${formatTs(r.created_at)} · ${r.created_by || '—'}` },
    {
      title: '发布',
      key: 'published',
      render: (_v, r) => (r.published_at ? `${formatTs(r.published_at)} · ${r.published_by || '—'}` : '—'),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_v, r) => (
        <Space>
          {r.status === 'draft' && (
            <Button
              size="small"
              type="primary"
              onClick={() => setStatusTarget({ item: detailName!, version: r.version, action: 'publish' })}
            >
              发布
            </Button>
          )}
          {r.status !== 'yanked' && (
            <Button
              size="small"
              danger
              onClick={() => setStatusTarget({ item: detailName!, version: r.version, action: 'yank' })}
            >
              下架
            </Button>
          )}
        </Space>
      ),
    },
  ]

  const auditColumns: ColumnsType<MarketAuditItem> = [
    { title: '时间', dataIndex: 'created_at', render: formatTs, width: 150 },
    { title: '插件', dataIndex: 'item_name' },
    { title: '版本', dataIndex: 'version', render: (v: string) => v || '—' },
    { title: '动作', dataIndex: 'action', render: (a: MarketAuditItem['action']) => <Tag>{ACTION_LABELS[a] ?? a}</Tag> },
    { title: '理由', dataIndex: 'reason', render: (v: string) => v || '—' },
    { title: '操作人', dataIndex: 'actor', render: (v: string) => v || '—' },
    {
      title: '详情',
      dataIndex: 'detail',
      ellipsis: true,
      render: (d: Record<string, unknown>) =>
        Object.keys(d ?? {}).length ? <Typography.Text code>{JSON.stringify(d)}</Typography.Text> : '—',
    },
  ]

  const statsColumns: ColumnsType<MarketDownloadStatsItem> = [
    { title: '插件', dataIndex: 'item_name' },
    { title: '人', dataIndex: 'user_ref', render: (v: number | null) => userName(v) },
    { title: '设备数', dataIndex: 'devices' },
    { title: '下载次数', dataIndex: 'downloads' },
    { title: '最近一次', dataIndex: 'last_at', render: formatTs },
  ]

  const downloadColumns: ColumnsType<MarketDownloadItem> = [
    { title: '时间', dataIndex: 'created_at', render: formatTs },
    { title: '版本', dataIndex: 'version' },
    { title: '人', dataIndex: 'user_ref', render: (v: number | null) => userName(v) },
    { title: '设备', dataIndex: 'device_id', render: (v: string) => <Typography.Text code>{v}</Typography.Text> },
  ]

  const latestDraftOrPublished = detail?.versions.find(v => v.status !== 'yanked') ?? detail?.versions[0]

  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      <Card
        title="插件市场"
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            登记插件
          </Button>
        }
      >
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          流程：登记插件 → 上传 tar.gz（草稿）→ 核对组件清单 → 写理由发布。员工用{' '}
          <Typography.Text code>/plugin install &lt;name&gt;@company</Typography.Text>{' '}
          安装，只看得到自己组织 / 团队可见的已发布版本。版本不可覆盖；下架后不能恢复，要修请升版本号重传。
        </Typography.Paragraph>
        <Table rowKey="id" columns={columns} dataSource={items ?? []} loading={isLoading} pagination={false} />
      </Card>

      <Card>
        <Tabs
          items={[
            {
              key: 'stats',
              label: '谁装了什么（近 30 天）',
              children: (
                <>
                  <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
                    按插件 × 人统计制品下载，人取自设备凭据（强归因）。下载不等于装成功，客户端校验失败会重下，次数只多不少。
                  </Typography.Paragraph>
                  <Table
                    rowKey={r => `${r.item_name}-${r.user_ref ?? 'none'}`}
                    columns={statsColumns}
                    dataSource={stats ?? []}
                    loading={statsLoading}
                    pagination={{ pageSize: 20 }}
                    size="small"
                  />
                </>
              ),
            },
            {
              key: 'audit',
              label: '变更审计',
              children: (
                <>
                  <Space style={{ marginBottom: 12 }}>
                    <Select
                      allowClear
                      placeholder="全部插件"
                      style={{ width: 220 }}
                      value={auditName}
                      onChange={setAuditName}
                      options={(items ?? []).map(i => ({ value: i.name, label: i.name }))}
                    />
                  </Space>
                  <Table
                    rowKey="id"
                    columns={auditColumns}
                    dataSource={audit ?? []}
                    loading={auditLoading}
                    pagination={{ pageSize: 20 }}
                    size="small"
                  />
                </>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        title={itemModal?.mode === 'edit' ? `编辑 ${itemModal.item.name}` : '登记插件'}
        open={itemModal !== null}
        onCancel={closeItemModal}
        onOk={() => itemForm.submit()}
        confirmLoading={itemMutation.isPending}
        destroyOnHidden
      >
        <Form form={itemForm} layout="vertical" onFinish={values => itemMutation.mutate(values)}>
          <Form.Item
            name="name"
            label="名称"
            extra="必须与插件包里 plugin.json 的 name 一致。登记后不能改：它是员工本地安装记录里的身份。"
            rules={[
              { required: true, message: '名称必填' },
              { pattern: /^[a-z0-9][a-z0-9-_]{0,63}$/, message: '小写字母、数字、-、_，以字母或数字开头，≤64' },
            ]}
          >
            <Input placeholder="feishu-docs" disabled={itemModal?.mode === 'edit'} />
          </Form.Item>
          <Form.Item name="kind" label="类型" rules={[{ required: true }]}>
            <Select
              options={(Object.keys(KIND_LABELS) as MarketKind[]).map(k => ({ value: k, label: KIND_LABELS[k] }))}
            />
          </Form.Item>
          <Form.Item name="description" label="目录描述" extra="留空则用 plugin.json 的 description">
            <Input.TextArea rows={2} maxLength={1024} />
          </Form.Item>
          <Form.Item name="maintainer" label="维护人">
            <Input maxLength={128} />
          </Form.Item>
          <Form.Item name="org_id" label="可见组织" rules={[{ required: true, message: '组织必填' }]}>
            <Select
              showSearch
              options={(orgs ?? []).map(o => ({ value: o.org_id, label: `${o.name}（${o.org_id}）` }))}
            />
          </Form.Item>
          <Form.Item name="team_id" label="可见团队" extra="留空 = 整个组织可见；填了只对该组织内这个团队可见">
            <Input placeholder="infra" maxLength={128} />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={detail ? `${detail.name} · ${scopeText(detail)}` : ''}
        open={detailName !== null}
        onClose={() => {
          setDetailName(null)
          setUploadFile(null)
        }}
        size={960}
        destroyOnHidden
      >
        {detail && (
          <Space orientation="vertical" size="large" style={{ width: '100%' }}>
            <Card size="small" title="上传新版本">
              <Upload.Dragger
                accept=".tar.gz,.tgz,application/gzip"
                maxCount={1}
                beforeUpload={file => {
                  if (file.size > MAX_PACKAGE_MB * 1024 * 1024) {
                    message.error(`包超过 ${MAX_PACKAGE_MB} MB`)
                    return Upload.LIST_IGNORE
                  }
                  setUploadFile(file)
                  return false
                }}
                onRemove={() => setUploadFile(null)}
                fileList={
                  uploadFile ? [{ uid: 'pkg', name: uploadFile.name, status: 'done', size: uploadFile.size }] : []
                }
              >
                <p className="ant-upload-drag-icon">
                  <InboxOutlined />
                </p>
                <p className="ant-upload-text">拖入或点击选择插件包（.tar.gz，plugin.json 在包根目录）</p>
                <p className="ant-upload-hint">
                  版本号取自 plugin.json，必须是 semver。含 ..、绝对路径或链接的包会被拒绝。
                </p>
              </Upload.Dragger>
              <Button
                type="primary"
                style={{ marginTop: 12 }}
                disabled={!uploadFile}
                loading={uploadMutation.isPending}
                onClick={() => uploadFile && uploadMutation.mutate({ name: detail.name, file: uploadFile })}
              >
                上传为草稿
              </Button>
            </Card>

            <Card size="small" title="版本">
              <Table
                rowKey="id"
                columns={versionColumns}
                dataSource={detail.versions}
                pagination={false}
                size="small"
                expandable={{
                  expandedRowRender: v => <ComponentsView components={v.components} />,
                  defaultExpandedRowKeys: latestDraftOrPublished ? [latestDraftOrPublished.id] : [],
                }}
              />
            </Card>

            <Card size="small" title="下载记录（最近 100 条）">
              <Table
                rowKey="id"
                columns={downloadColumns}
                dataSource={downloads ?? []}
                loading={downloadsLoading}
                pagination={{ pageSize: 10 }}
                size="small"
              />
            </Card>
          </Space>
        )}
      </Drawer>

      <Modal
        title={
          statusTarget?.action === 'publish'
            ? `发布 ${statusTarget.item}@${statusTarget.version}`
            : `下架 ${statusTarget?.item}@${statusTarget?.version}`
        }
        open={statusTarget !== null}
        onCancel={() => {
          setStatusTarget(null)
          setReason('')
        }}
        okText={statusTarget?.action === 'publish' ? '发布' : '下架'}
        okButtonProps={{ danger: statusTarget?.action === 'yank', disabled: !reason.trim() }}
        confirmLoading={statusMutation.isPending}
        onOk={() => statusTarget && statusMutation.mutate({ ...statusTarget, reason: reason.trim() })}
      >
        {statusTarget?.action === 'publish' ? (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            title="发布后，可见范围内的员工就能安装。请先核对组件清单里的 Hooks 与 MCP 地址：hooks 会在员工机器上执行命令，客户端不会再弹信任确认。"
          />
        ) : (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            title="下架后不再出现在目录里、不能再下载；已经装了的员工照常可用。下架不可撤销。"
          />
        )}
        <Input.TextArea
          rows={3}
          placeholder="理由（必填，进审计）"
          value={reason}
          maxLength={512}
          onChange={e => setReason(e.target.value)}
        />
      </Modal>
    </Space>
  )
}
