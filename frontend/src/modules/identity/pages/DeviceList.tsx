import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import {
  createEnrollCode,
  fetchDevices,
  fetchEnrollCodes,
  revokeDevice,
} from '../services/api'
import type { DeviceListItem, EnrollCodeItem } from '../types/identity'

function formatTs(value: string | null | undefined) {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '—'
}

export default function DeviceList() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const deviceFromUrl = searchParams.get('device_id') || undefined
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [createOpen, setCreateOpen] = useState(false)
  const [issuedCode, setIssuedCode] = useState<string | null>(null)
  const [form] = Form.useForm()

  const { data, isLoading } = useQuery({
    queryKey: ['identity-devices', page, pageSize, deviceFromUrl],
    queryFn: () => fetchDevices({ page, page_size: pageSize, device_id: deviceFromUrl }),
  })

  const { data: codes, isLoading: codesLoading } = useQuery({
    queryKey: ['identity-enroll-codes'],
    queryFn: fetchEnrollCodes,
  })

  const issueMutation = useMutation({
    mutationFn: createEnrollCode,
    onSuccess: async created => {
      setIssuedCode(created.code)
      form.resetFields()
      await queryClient.invalidateQueries({ queryKey: ['identity-enroll-codes'] })
    },
    onError: () => message.error('签发注册码失败'),
  })

  const revokeMutation = useMutation({
    mutationFn: revokeDevice,
    onSuccess: async () => {
      message.success('已吊销该设备全部凭据')
      await queryClient.invalidateQueries({ queryKey: ['identity-devices'] })
    },
    onError: () => message.error('吊销失败'),
  })

  const deviceColumns: ColumnsType<DeviceListItem> = [
    {
      title: '设备',
      dataIndex: 'device_id',
      ellipsis: true,
    },
    {
      title: '组织',
      dataIndex: 'org_id',
      width: 140,
    },
    {
      title: '团队',
      dataIndex: 'team_id',
      width: 120,
      render: (value: string) => value || '—',
    },
    {
      title: '用户',
      dataIndex: 'user_id',
      width: 180,
      ellipsis: true,
      render: (value: string) => value || '—',
    },
    {
      title: '平台 / 版本',
      key: 'platform',
      width: 160,
      render: (_value, record) => (
        <span>
          {record.platform || '—'} {record.ver ? `/ ${record.ver}` : ''}
        </span>
      ),
    },
    {
      title: '最近活跃',
      dataIndex: 'last_seen_at',
      width: 160,
      render: formatTs,
    },
    {
      title: '状态',
      key: 'status',
      width: 90,
      render: (_value, record) =>
        record.revoked ? <Tag color="red">已吊销</Tag> : <Tag color="green">有效</Tag>,
    },
    {
      title: '操作',
      key: 'action',
      width: 220,
      render: (_value, record) => (
        <Space size="small">
          <Button
            type="link"
            style={{ padding: 0 }}
            onClick={() => navigate(`/trajectories?device_id=${encodeURIComponent(record.device_id)}`)}
          >
            轨迹
          </Button>
          <Button
            type="link"
            style={{ padding: 0 }}
            onClick={() => navigate(`/audit?device_id=${encodeURIComponent(record.device_id)}`)}
          >
            审计
          </Button>
          <Popconfirm
            title="吊销后该设备立即无法访问控制面，确认？"
            onConfirm={() => revokeMutation.mutate(record.device_id)}
            disabled={record.revoked}
          >
            <Button type="link" danger disabled={record.revoked} loading={revokeMutation.isPending}>
              吊销
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const codeColumns: ColumnsType<EnrollCodeItem> = [
    { title: '组织', dataIndex: 'org_id', width: 140 },
    { title: '团队', dataIndex: 'team_id', width: 120, render: (v: string) => v || '—' },
    { title: '备注', dataIndex: 'note', ellipsis: true, render: (v: string) => v || '—' },
    { title: '创建', dataIndex: 'created_at', width: 160, render: formatTs },
    { title: '过期', dataIndex: 'expires_at', width: 160, render: formatTs },
    {
      title: '状态',
      key: 'used',
      width: 90,
      render: (_v, record) =>
        record.used_at ? <Tag>已使用</Tag> : <Tag color="blue">未用</Tag>,
    },
  ]

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <Typography.Title level={4} style={{ color: '#fff', margin: 0 }}>
        设备
      </Typography.Title>
      <Typography.Paragraph style={{ color: '#8c8c8c', marginTop: -8 }}>
        一次性注册码换设备凭据。明文只在签发当下展示一次，库里只存 sha256。
        客户端携带 <code>Authorization: Bearer</code> 访问 <code>/api/v1/ctl/*</code>。
      </Typography.Paragraph>

      <Card
        size="small"
        title="设备列表"
        extra={
          <Button type="primary" onClick={() => { setIssuedCode(null); setCreateOpen(true) }}>
            签发注册码
          </Button>
        }
      >
        <Table
          rowKey="device_id"
          columns={deviceColumns}
          dataSource={data?.items || []}
          loading={isLoading}
          pagination={{
            current: page,
            pageSize,
            total: data?.total || 0,
            showSizeChanger: true,
            showTotal: total => `共 ${total} 台`,
            onChange: (nextPage, nextSize) => {
              setPage(nextPage)
              setPageSize(nextSize)
            },
          }}
          size="small"
        />
      </Card>

      <Card size="small" title="注册码">
        <Table
          rowKey="id"
          columns={codeColumns}
          dataSource={codes || []}
          loading={codesLoading}
          pagination={false}
          size="small"
        />
      </Card>

      <Modal
        title="签发一次性注册码"
        open={createOpen}
        onCancel={() => { setCreateOpen(false); setIssuedCode(null) }}
        footer={issuedCode ? (
          <Button type="primary" onClick={() => { setCreateOpen(false); setIssuedCode(null) }}>
            已复制，关闭
          </Button>
        ) : undefined}
        onOk={issuedCode ? undefined : async () => {
          const values = await form.validateFields()
          await issueMutation.mutateAsync(values)
        }}
        confirmLoading={issueMutation.isPending}
        destroyOnHidden
      >
        {issuedCode ? (
          <Space orientation="vertical" style={{ width: '100%' }}>
            <Typography.Text>明文只这一次。交给设备后让它 POST /api/v1/ctl/enroll。</Typography.Text>
            <Typography.Paragraph copyable style={{ fontFamily: 'monospace' }}>
              {issuedCode}
            </Typography.Paragraph>
          </Space>
        ) : (
          <Form form={form} layout="vertical">
            <Form.Item name="org_id" label="组织 ID" rules={[{ required: true, message: '必填' }]}>
              <Input placeholder="corp-shanghai" />
            </Form.Item>
            <Form.Item name="org_name" label="组织名称">
              <Input placeholder="可选，首次创建组织时使用" />
            </Form.Item>
            <Form.Item name="team_id" label="团队 ID">
              <Input placeholder="infra-platform" />
            </Form.Item>
            <Form.Item name="note" label="备注">
              <Input placeholder="发给谁、哪台机器" />
            </Form.Item>
          </Form>
        )}
      </Modal>
    </div>
  )
}
