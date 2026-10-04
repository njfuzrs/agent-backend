import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Card, Popconfirm, Select, Space, Table, Tag, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import { fetchAuthAudit, fetchUsers, restoreUser, revokeUser, setUserRole } from '../services/api'
import type { AuthAuditItem, UserItem } from '../types/auth'

function formatTs(value: string | null | undefined) {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '—'
}

// 后端 auth_audit.event 的取值，见 backend/app/modules/auth/service/users.py
const EVENT_LABELS: Record<string, string> = {
  login: '飞书登录',
  logout: '登出',
  break_glass: '口令应急登录',
  revoke: '吊销',
  restore: '恢复',
  role_change: '改角色',
  login_rejected: '登录被拒',
  cli_login: 'CLI 飞书授权',
  cli_exchange: 'CLI 登录',
  cli_conflict: 'CLI 设备冲突',
  cli_logout: 'CLI 登出',
}

function errorDetail(err: unknown): string {
  const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  if (detail === 'cannot demote yourself') return '不能把自己降为 member'
  if (detail === 'cannot revoke yourself') return '不能吊销自己'
  return '操作失败'
}

export default function UserList() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [auditUser, setAuditUser] = useState<number | undefined>(undefined)

  const { data: users, isLoading } = useQuery({ queryKey: ['auth-users'], queryFn: fetchUsers })
  const { data: audit, isLoading: auditLoading } = useQuery({
    queryKey: ['auth-audit', auditUser],
    queryFn: () => fetchAuthAudit(auditUser),
  })

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['auth-users'] })
    queryClient.invalidateQueries({ queryKey: ['auth-audit'] })
  }
  const roleMutation = useMutation({
    mutationFn: ({ id, role }: { id: number; role: UserItem['role'] }) => setUserRole(id, role),
    onSuccess: () => {
      message.success('角色已更新')
      refresh()
    },
    onError: err => message.error(errorDetail(err)),
  })
  const statusMutation = useMutation({
    mutationFn: ({ id, revoke }: { id: number; revoke: boolean }) => (revoke ? revokeUser(id) : restoreUser(id)),
    onSuccess: (_d, v) => {
      message.success(v.revoke ? '已吊销，该用户的会话下一次请求即失效' : '已恢复')
      refresh()
    },
    onError: err => message.error(errorDetail(err)),
  })

  const userName = (id: number | null) => users?.find(u => u.id === id)?.name || (id ?? '—')

  const columns: ColumnsType<UserItem> = [
    { title: '姓名', dataIndex: 'name', render: (v: string, r) => v || r.union_id },
    { title: '邮箱', dataIndex: 'email', render: (v: string) => v || '—' },
    {
      title: '角色',
      dataIndex: 'role',
      render: (role: UserItem['role'], r) => (
        <Select
          size="small"
          value={role}
          style={{ width: 110 }}
          disabled={r.status !== 'active'}
          loading={roleMutation.isPending && roleMutation.variables?.id === r.id}
          onChange={value => roleMutation.mutate({ id: r.id, role: value })}
          options={[
            { value: 'admin', label: 'admin' },
            { value: 'member', label: 'member' },
          ]}
        />
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      render: (s: UserItem['status']) => (s === 'active' ? <Tag color="green">正常</Tag> : <Tag color="red">已吊销</Tag>),
    },
    { title: '设备', dataIndex: 'device_count' },
    { title: '最近登录', dataIndex: 'last_login_at', render: formatTs },
    { title: '首次登录', dataIndex: 'created_at', render: formatTs },
    {
      title: '操作',
      key: 'actions',
      render: (_v, r) => (
        <Space>
          {r.status === 'active' ? (
            <Popconfirm
              title="吊销该用户？"
              description="已登录的会话会在下一次请求时失效，之后也无法再用飞书登录。"
              onConfirm={() => statusMutation.mutate({ id: r.id, revoke: true })}
            >
              <Button size="small" danger>吊销</Button>
            </Popconfirm>
          ) : (
            <Button size="small" onClick={() => statusMutation.mutate({ id: r.id, revoke: false })}>恢复</Button>
          )}
          <Button size="small" type="link" onClick={() => setAuditUser(r.id)}>登录记录</Button>
          <Button size="small" type="link" onClick={() => navigate(`/cost?user_ref=${r.id}`)}>用量</Button>
        </Space>
      ),
    },
  ]

  const auditColumns: ColumnsType<AuthAuditItem> = [
    { title: '时间', dataIndex: 'created_at', render: formatTs, width: 160 },
    { title: '事件', dataIndex: 'event', render: (e: string) => EVENT_LABELS[e] || e },
    { title: '对象', dataIndex: 'user_id', render: (id: number | null) => userName(id) },
    { title: '操作者', dataIndex: 'actor' },
    { title: '详情', dataIndex: 'detail_json', render: (v: string | null) => v || '—' },
  ]

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Card title="用户">
        <Typography.Paragraph type="secondary">
          飞书登录过的人都在这里。新用户默认是 member，看不到管理台；admin 由这里授予。
          引导管理员由服务器 .env 的 ADMIN_BOOTSTRAP_UNION_IDS 指定。
        </Typography.Paragraph>
        <Table rowKey="id" loading={isLoading} dataSource={users} columns={columns} pagination={false} />
      </Card>
      <Card
        title="登录审计"
        extra={auditUser !== undefined ? <Button size="small" onClick={() => setAuditUser(undefined)}>看全部</Button> : null}
      >
        <Table
          rowKey="id"
          size="small"
          loading={auditLoading}
          dataSource={audit}
          columns={auditColumns}
          pagination={{ pageSize: 20 }}
        />
      </Card>
    </Space>
  )
}
