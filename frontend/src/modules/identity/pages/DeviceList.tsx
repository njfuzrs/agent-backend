import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import { fetchBridgeSessions } from '../../bridge/services/api'
import {
  createEnrollCode,
  fetchDevices,
  fetchEnrollCodes,
  fetchOrganizations,
  fetchTeams,
  moveDevice,
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
  // 正在调整归属的设备；null = 弹窗关闭
  const [moving, setMoving] = useState<DeviceListItem | null>(null)
  const [moveForm] = Form.useForm()
  const moveOrgId = Form.useWatch('org_id', moveForm) as string | undefined

  const { data: orgs } = useQuery({
    queryKey: ['identity-orgs'],
    queryFn: fetchOrganizations,
    enabled: moving !== null,
  })
  const { data: moveTeams, isFetching: moveTeamsLoading } = useQuery({
    queryKey: ['identity-teams', moveOrgId],
    queryFn: () => fetchTeams(moveOrgId),
    enabled: moving !== null && !!moveOrgId,
  })

  const moveMutation = useMutation({
    mutationFn: (values: { org_id: string; team_id?: string; reason: string }) =>
      moveDevice(moving!.device_id, { ...values, team_id: values.team_id || null }),
    onSuccess: async result => {
      message.success(result.changed ? '已调整，设备下一次请求控制面即生效' : '归属没有变化')
      setMoving(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['identity-devices'] }),
        queryClient.invalidateQueries({ queryKey: ['identity-teams'] }),
        queryClient.invalidateQueries({ queryKey: ['identity-orgs'] }),
        queryClient.invalidateQueries({ queryKey: ['identity-audit'] }),
      ])
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      message.error(typeof detail === 'string' ? detail : '调整归属失败')
    },
  })

  const openMove = (record: DeviceListItem) => {
    moveForm.resetFields()
    moveForm.setFieldsValue({ org_id: record.org_id, team_id: record.team_id || undefined })
    setMoving(record)
  }

  const { data, isLoading } = useQuery({
    queryKey: ['identity-devices', page, pageSize, deviceFromUrl],
    queryFn: () => fetchDevices({ page, page_size: pageSize, device_id: deviceFromUrl }),
  })

  const { data: codes, isLoading: codesLoading } = useQuery({
    queryKey: ['identity-enroll-codes'],
    queryFn: fetchEnrollCodes,
  })

  // 只要 paired。waiting 还没有人在遥控，不点绿。
  const { data: bridge } = useQuery({
    queryKey: ['bridge-sessions', { state: 'paired' }],
    queryFn: () => fetchBridgeSessions({ state: 'paired' }),
    refetchInterval: 15_000,
  })
  const pairedDevices = useMemo(
    () => new Set((bridge?.items ?? []).map(item => item.device_id)),
    [bridge?.items],
  )

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
      width: 150,
      render: (_value, record) => (
        <Space size={4}>
          {record.revoked ? <Tag color="red">已吊销</Tag> : <Tag color="green">有效</Tag>}
          {pairedDevices.has(record.device_id) && (
            <Button
              type="link"
              style={{ padding: 0 }}
              onClick={() => navigate(`/bridge?device_id=${encodeURIComponent(record.device_id)}`)}
            >
              <span
                style={{
                  display: 'inline-block',
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  background: '#52c41a',
                  marginRight: 4,
                }}
              />
              遥控中
            </Button>
          )}
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 340,
      render: (_value, record) => (
        <Space size="small">
          <Button type="link" style={{ padding: 0 }} onClick={() => openMove(record)}>
            调整归属
          </Button>
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
          {/* 只给链接，不在这里加 cost_usd 列：那要把 by-scope 打进设备列表的每次渲染，
              而且周期不明（列头写「成本」但没人知道是本月还是全部）。 */}
          <Button
            type="link"
            style={{ padding: 0 }}
            onClick={() => navigate(`/cost?device_id=${encodeURIComponent(record.device_id)}`)}
          >
            成本
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
        title="调整设备归属"
        open={moving !== null}
        onCancel={() => setMoving(null)}
        onOk={async () => {
          const values = await moveForm.validateFields()
          await moveMutation.mutateAsync(values)
        }}
        confirmLoading={moveMutation.isPending}
        destroyOnHidden
      >
        <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
          设备：<code>{moving?.device_id}</code>。凭据不重签，设备下一次请求控制面就按新的组织 / 团队命中策略与预算；
          重新 <code>sid-code login</code> 也会保留这里的设置。团队到「组织与团队」页新建。
        </Typography.Paragraph>
        <Form form={moveForm} layout="vertical">
          <Form.Item name="org_id" label="组织" rules={[{ required: true, message: '必选' }]}>
            <Select
              showSearch
              options={(orgs || []).map(o => ({ value: o.org_id, label: `${o.org_id}（${o.name}）` }))}
              onChange={() => moveForm.setFieldsValue({ team_id: undefined })}
            />
          </Form.Item>
          <Form.Item name="team_id" label="团队" extra="留空 = 只挂在组织上，不进任何团队">
            <Select
              allowClear
              showSearch
              loading={moveTeamsLoading}
              placeholder="不进团队"
              options={(moveTeams || []).map(t => ({ value: t.team_id, label: `${t.team_id}（${t.name}）` }))}
            />
          </Form.Item>
          <Form.Item name="reason" label="理由" rules={[{ required: true, whitespace: true, message: '必填' }]}>
            <Input.TextArea rows={2} placeholder="写进审计" />
          </Form.Item>
        </Form>
      </Modal>

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
