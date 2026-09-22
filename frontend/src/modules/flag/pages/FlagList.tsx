import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import {
  createFlag,
  deleteFlag,
  fetchFlagAudit,
  fetchFlags,
  setFlagEnabled,
  updateFlag,
} from '../services/api'
import type { FlagAuditItem, FlagItem, FlagValue } from '../types/flag'

function formatTs(value: string | null | undefined) {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '—'
}

/** 值用 JSON 字面量展示/编辑：客户端要的是原生类型，`true` 和 `"true"` 是两回事。 */
function formatValue(value: FlagValue | null | undefined) {
  if (value === null || value === undefined) return '—'
  return JSON.stringify(value)
}

/** 从后端 422 里取出门禁给的那句话，直接给人看——门禁的 detail 已经写清为什么拒。 */
function errorText(err: unknown, fallback: string) {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: string }
    if (first?.msg) return first.msg
  }
  return fallback
}

type FormValues = {
  key: string
  value: string
  description?: string
  reason?: string
}

export default function FlagList() {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<FlagItem | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [auditKey, setAuditKey] = useState<string | null>(null)
  const [form] = Form.useForm<FormValues>()

  const { data: flags, isLoading } = useQuery({
    queryKey: ['flags'],
    queryFn: fetchFlags,
  })

  const { data: audit, isLoading: auditLoading } = useQuery({
    queryKey: ['flag-audit', auditKey],
    queryFn: () => fetchFlagAudit(auditKey ?? undefined),
    enabled: auditKey !== null,
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['flags'] })

  const closeModal = () => {
    setCreateOpen(false)
    setEditing(null)
    form.resetFields()
  }

  const saveMutation = useMutation({
    mutationFn: async (values: FormValues) => {
      // 解析放在提交时而不是 onChange：编辑中途的半截 JSON 不该弹错
      let parsed: FlagValue
      try {
        parsed = JSON.parse(values.value)
      } catch {
        throw new Error(
          '值必须是合法 JSON：true / false / 40 / "stable" / {"tool_use":0.5}。字符串要带双引号。',
        )
      }
      const payload = {
        value: parsed,
        description: values.description ?? '',
        reason: values.reason ?? '',
      }
      return editing
        ? updateFlag(editing.key, payload)
        : createFlag({ key: values.key, ...payload })
    },
    onSuccess: async () => {
      message.success(editing ? '已更新' : '已创建')
      closeModal()
      await invalidate()
    },
    onError: (err: Error) => message.error(errorText(err, err.message || '保存失败')),
  })

  const toggleMutation = useMutation({
    mutationFn: ({ key, enabled }: { key: string; enabled: boolean }) =>
      setFlagEnabled(key, enabled),
    onSuccess: async (_data, vars) => {
      message.success(vars.enabled ? '已启用' : '已停用，客户端下次刷新会回落默认值')
      await invalidate()
    },
    onError: err => message.error(errorText(err, '操作失败')),
  })

  const deleteMutation = useMutation({
    mutationFn: (key: string) => deleteFlag(key),
    onSuccess: async () => {
      message.success('已删除，审计记录保留')
      await invalidate()
    },
    onError: err => message.error(errorText(err, '删除失败')),
  })

  const openCreate = () => {
    form.resetFields()
    setEditing(null)
    setCreateOpen(true)
  }

  const openEdit = (flag: FlagItem) => {
    setEditing(flag)
    form.setFieldsValue({
      key: flag.key,
      value: JSON.stringify(flag.value),
      description: flag.description,
      reason: '',
    })
  }

  const columns: ColumnsType<FlagItem> = [
    {
      title: 'Key',
      dataIndex: 'key',
      render: (key: string, row) => (
        <Space>
          <Typography.Text code>{key}</Typography.Text>
          {row.disabled && <Tag color="default">已停用</Tag>}
        </Space>
      ),
    },
    {
      title: '值',
      dataIndex: 'value',
      render: (value: FlagValue) => <Typography.Text code>{formatValue(value)}</Typography.Text>,
    },
    { title: '说明', dataIndex: 'description', ellipsis: true },
    {
      title: '下发',
      dataIndex: 'disabled',
      width: 90,
      render: (disabled: boolean) =>
        disabled ? (
          <Tooltip title="不出现在 /ctl/flags 响应里，客户端按「远程已删除」回落默认值">
            <Tag color="default">不下发</Tag>
          </Tooltip>
        ) : (
          <Tag color="green">下发中</Tag>
        ),
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
            改值
          </Button>
          <Button
            size="small"
            onClick={() => toggleMutation.mutate({ key: row.key, enabled: row.disabled })}
          >
            {row.disabled ? '启用' : '停用'}
          </Button>
          <Button size="small" onClick={() => setAuditKey(row.key)}>
            审计
          </Button>
          <Popconfirm
            title={`删除 ${row.key}？`}
            description="客户端下次刷新会回落默认值。审计记录保留。"
            okButtonProps={{ danger: true }}
            onConfirm={() => deleteMutation.mutate(row.key)}
          >
            <Button size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const auditColumns: ColumnsType<FlagAuditItem> = [
    { title: '时间', dataIndex: 'created_at', width: 150, render: formatTs },
    { title: 'Key', dataIndex: 'key', render: (k: string) => <Typography.Text code>{k}</Typography.Text> },
    { title: '动作', dataIndex: 'action', width: 90 },
    {
      title: '变更',
      key: 'change',
      render: (_, row) => (
        <Typography.Text code>
          {formatValue(row.old_value)} → {formatValue(row.new_value)}
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
        message="Flag 只能施加约束，不能放宽安全限制"
        description={
          <span>
            下发端点 <Typography.Text code>GET /traj/api/v1/ctl/flags</Typography.Text> 无需认证（客户端契约如此），
            所以带 bypass / disable_sandbox / disable_all_hooks 一类词的 key 会被后端拒掉。放宽类开关走 M3 Policy
            （设备凭据 + TLS）。key 必须是 <Typography.Text code>snake_case</Typography.Text>，
            客户端用 <Typography.Text code>SID_CODE_FLAG_&lt;KEY&gt;</Typography.Text> 做环境变量覆盖。
          </span>
        }
      />

      <Card
        title="Feature Flags"
        extra={
          <Space>
            <Button onClick={() => setAuditKey('')}>全部审计</Button>
            <Button type="primary" onClick={openCreate}>
              新建 Flag
            </Button>
          </Space>
        }
      >
        <Table
          rowKey="key"
          loading={isLoading}
          columns={columns}
          dataSource={flags ?? []}
          pagination={false}
        />
      </Card>

      <Modal
        open={createOpen || editing !== null}
        title={editing ? `修改 ${editing.key}` : '新建 Flag'}
        onCancel={closeModal}
        onOk={() => form.submit()}
        confirmLoading={saveMutation.isPending}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={values => saveMutation.mutate(values)}>
          <Form.Item
            name="key"
            label="Key"
            rules={[
              { required: true, message: '必填' },
              {
                pattern: /^[a-z][a-z0-9_]*$/,
                message: 'snake_case：小写字母开头，只含小写字母/数字/下划线',
              },
            ]}
            extra="客户端会用 SID_CODE_FLAG_<KEY> 作为环境变量覆盖名，所以必须是合法标识符"
          >
            <Input placeholder="content_tracing" disabled={editing !== null} />
          </Form.Item>
          <Form.Item
            name="value"
            label="值（JSON）"
            rules={[{ required: true, message: '必填' }]}
            extra='原生 JSON 类型：true / false / 40 / "stable" / {"tool_use":0.5}。字符串要带双引号。'
          >
            <Input.TextArea rows={3} placeholder="true" />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input placeholder="这个开关控制什么" />
          </Form.Item>
          <Form.Item name="reason" label="变更原因" extra="写进审计，之后查「为什么改成这样」靠它">
            <Input placeholder="灰度 20% / 事故复盘收紧" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        open={auditKey !== null}
        title={auditKey ? `${auditKey} 变更记录` : '全部变更记录'}
        footer={null}
        width={900}
        onCancel={() => setAuditKey(null)}
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
