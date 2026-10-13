import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Form, Input, InputNumber, Modal, Select, Space, Table, Tag, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { PlusOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { fetchOrganizations } from '../../identity/services/api'
import { fetchUploadTokenAudit, fetchUploadTokens, issueUploadToken, revokeUploadToken } from '../services/api'
import type { UploadTokenAuditItem, UploadTokenIssued, UploadTokenItem } from '../types/marketplace'

const STATUS_TAGS: Record<UploadTokenItem['status'], { color: string; label: string }> = {
  active: { color: 'green', label: '有效' },
  revoked: { color: 'red', label: '已吊销' },
  expired: { color: 'default', label: '已过期' },
}

const ACTION_LABELS: Record<UploadTokenAuditItem['action'], string> = {
  issue: '签发',
  revoke: '吊销',
  upload: '上传草稿',
  denied: '越权被拒',
}

function formatTs(value: string | null | undefined) {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '—'
}

function errorText(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: string }
    if (first?.msg) return first.msg
  }
  return (err as Error)?.message || fallback
}

type IssueForm = { name: string; org_id: string; expires_in_days: number; reason: string }

/** CI 上传凭据：只能把包传成草稿，发布 / 下架仍要管理员在本页人工核对后操作。 */
export default function UploadTokensPanel() {
  const queryClient = useQueryClient()
  const [form] = Form.useForm<IssueForm>()
  const [issueOpen, setIssueOpen] = useState(false)
  const [issued, setIssued] = useState<UploadTokenIssued | null>(null)
  const [revokeTarget, setRevokeTarget] = useState<UploadTokenItem | null>(null)
  const [reason, setReason] = useState('')

  const { data: tokens, isLoading } = useQuery({ queryKey: ['market-upload-tokens'], queryFn: fetchUploadTokens })
  const { data: audit, isLoading: auditLoading } = useQuery({
    queryKey: ['market-upload-token-audit'],
    queryFn: fetchUploadTokenAudit,
  })
  const { data: orgs } = useQuery({ queryKey: ['identity-organizations'], queryFn: fetchOrganizations })

  const invalidate = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ['market-upload-tokens'] }),
      queryClient.invalidateQueries({ queryKey: ['market-upload-token-audit'] }),
    ])

  const issueMutation = useMutation({
    mutationFn: (values: IssueForm) => issueUploadToken({ ...values, reason: values.reason.trim() }),
    onSuccess: async data => {
      setIssueOpen(false)
      form.resetFields()
      setIssued(data)
      await invalidate()
    },
    onError: err => message.error(errorText(err, '签发失败')),
  })

  const revokeMutation = useMutation({
    mutationFn: ({ id, why }: { id: number; why: string }) => revokeUploadToken(id, why),
    onSuccess: async () => {
      message.success('已吊销，下一次使用即被拒绝')
      setRevokeTarget(null)
      setReason('')
      await invalidate()
    },
    onError: err => message.error(errorText(err, '吊销失败')),
  })

  const openIssue = () => {
    form.setFieldsValue({ expires_in_days: 90, org_id: orgs?.[0]?.org_id })
    setIssueOpen(true)
  }

  const columns: ColumnsType<UploadTokenItem> = [
    { title: '名称', dataIndex: 'name', render: (v: string) => <Typography.Text code>{v}</Typography.Text> },
    { title: '组织', dataIndex: 'org_id' },
    {
      title: '状态',
      dataIndex: 'status',
      render: (s: UploadTokenItem['status']) => <Tag color={STATUS_TAGS[s].color}>{STATUS_TAGS[s].label}</Tag>,
    },
    { title: '到期', dataIndex: 'expires_at', render: formatTs },
    { title: '最近使用', dataIndex: 'last_used_at', render: formatTs },
    {
      title: '签发',
      dataIndex: 'created_at',
      render: (v: string, row) => `${formatTs(v)} · ${row.created_by || '—'}`,
    },
    {
      title: '操作',
      key: 'actions',
      render: (_, row) =>
        row.status === 'active' ? (
          <Button size="small" danger onClick={() => setRevokeTarget(row)}>
            吊销
          </Button>
        ) : row.revoked_at ? (
          <Typography.Text type="secondary">
            {formatTs(row.revoked_at)} · {row.revoked_by || '—'}
          </Typography.Text>
        ) : null,
    },
  ]

  const auditColumns: ColumnsType<UploadTokenAuditItem> = [
    { title: '时间', dataIndex: 'created_at', width: 150, render: formatTs },
    { title: '凭据', dataIndex: 'token_name' },
    {
      title: '动作',
      dataIndex: 'action',
      render: (a: UploadTokenAuditItem['action']) => (
        <Tag color={a === 'denied' ? 'red' : undefined}>{ACTION_LABELS[a] ?? a}</Tag>
      ),
    },
    {
      title: '详情',
      dataIndex: 'detail',
      ellipsis: true,
      render: (d: Record<string, unknown>) => {
        if (d.path) return `${d.method ?? ''} ${d.path}`
        if (d.item) return `${d.item}${d.version ? `@${d.version}` : ''}`
        return d.expires_at ? `到期 ${formatTs(String(d.expires_at))}` : '—'
      },
    },
    { title: '原因', dataIndex: 'reason', ellipsis: true },
    { title: '操作人', dataIndex: 'actor' },
  ]

  return (
    <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Typography.Text type="secondary">
          给私有仓 CI 用：只能把包上传成草稿（
          <Typography.Text code>POST /api/v1/marketplace/items/&lt;name&gt;/versions</Typography.Text>，
          <Typography.Text code>Authorization: Bearer mkt_…</Typography.Text>），且只能传到凭据所属组织的插件。
          发布、下架、登记、改可见范围一律 403；发布前请在本页核对 hooks 与 MCP 地址。
        </Typography.Text>
        <Button icon={<PlusOutlined />} onClick={openIssue}>
          签发凭据
        </Button>
      </Space>
      <Table rowKey="id" columns={columns} dataSource={tokens ?? []} loading={isLoading} pagination={false} size="small" />
      <Typography.Title level={5} style={{ margin: 0 }}>
        凭据审计
      </Typography.Title>
      <Table
        rowKey="id"
        columns={auditColumns}
        dataSource={audit ?? []}
        loading={auditLoading}
        pagination={{ pageSize: 20 }}
        size="small"
      />

      <Modal
        title="签发 CI 上传凭据"
        open={issueOpen}
        onCancel={() => setIssueOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={issueMutation.isPending}
        okText="签发"
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={values => issueMutation.mutate(values)}>
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, pattern: /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/, message: '字母数字开头，可含 . _ -，≤64' }]}
            extra="会出现在审计的操作人里（ci:<名称>#<id>），建议写仓库名。"
          >
            <Input placeholder="ci-acme-plugins" />
          </Form.Item>
          <Form.Item name="org_id" label="组织" rules={[{ required: true }]}>
            <Select options={(orgs ?? []).map(o => ({ value: o.org_id, label: `${o.name}（${o.org_id}）` }))} />
          </Form.Item>
          <Form.Item name="expires_in_days" label="有效期（天）" rules={[{ required: true }]} extra="到期不续期，需重新签发。">
            <InputNumber min={1} max={365} />
          </Form.Item>
          <Form.Item name="reason" label="签发理由" rules={[{ required: true, whitespace: true, message: '理由必填' }]}>
            <Input />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="凭据已签发"
        open={issued !== null}
        onCancel={() => setIssued(null)}
        footer={<Button onClick={() => setIssued(null)}>我已保存</Button>}
      >
        <Alert type="warning" showIcon message="明文只显示这一次，关闭后无法再查看。请存进 CI 的 secret。" />
        <Typography.Paragraph copyable={{ text: issued?.token }} code style={{ marginTop: 12, wordBreak: 'break-all' }}>
          {issued?.token}
        </Typography.Paragraph>
      </Modal>

      <Modal
        title={`吊销 ${revokeTarget?.name ?? ''}`}
        open={revokeTarget !== null}
        onCancel={() => {
          setRevokeTarget(null)
          setReason('')
        }}
        onOk={() => {
          if (!reason.trim()) {
            message.error('原因必填')
            return
          }
          if (revokeTarget) revokeMutation.mutate({ id: revokeTarget.id, why: reason.trim() })
        }}
        okButtonProps={{ danger: true }}
        okText="吊销"
        confirmLoading={revokeMutation.isPending}
      >
        <Typography.Paragraph type="secondary">吊销立即生效且不可恢复，需要时重新签发一把。</Typography.Paragraph>
        <Input placeholder="吊销原因（进审计）" value={reason} onChange={e => setReason(e.target.value)} />
      </Modal>
    </Space>
  )
}
