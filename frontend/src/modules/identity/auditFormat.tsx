import { Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import type { IdentityAuditItem } from './types/identity'

// identity_audit 的展示口径：「组织与团队」页与设备页的变更记录共用一份
export const IDENTITY_ACTION_LABEL: Record<string, string> = {
  org_create: '新建组织',
  org_rename: '组织改名',
  team_create: '新建团队',
  team_rename: '团队改名',
  device_move: '调整设备归属',
  device_revoke: '吊销设备',
  enroll_code_issue: '签发注册码',
}

function formatTs(value: string | null | undefined) {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '—'
}

export function describeIdentityDetail(r: IdentityAuditItem): string {
  const d = r.detail as {
    name?: string
    old_name?: string
    new_name?: string
    from?: { org_id?: string; team_id?: string }
    to?: { org_id?: string; team_id?: string }
    team_id?: string
    expires_at?: string
    revoked_credentials?: number
    org_created?: boolean
    team_created?: boolean
  }
  const slot = (s?: { org_id?: string; team_id?: string }) =>
    s ? `${s.org_id || '—'} / ${s.team_id || '无团队'}` : '—'
  if (d.from || d.to) return `${slot(d.from)} → ${slot(d.to)}`
  if (d.old_name !== undefined) return `${d.old_name} → ${d.new_name}`
  if (r.action === 'device_revoke') return `吊销 ${d.revoked_credentials ?? 0} 份有效凭据`
  if (r.action === 'enroll_code_issue') {
    const created = [d.org_created && '顺带建了组织', d.team_created && '顺带建了团队'].filter(Boolean).join('，')
    return `团队 ${d.team_id || '无'}，${formatTs(d.expires_at)} 过期${created ? `（${created}）` : ''}`
  }
  if (d.name) return `名称：${d.name}`
  return '—'
}

export const identityAuditColumns: ColumnsType<IdentityAuditItem> = [
  { title: '时间', dataIndex: 'created_at', width: 160, render: formatTs },
  {
    title: '动作',
    dataIndex: 'action',
    width: 120,
    render: (v: string) => <Tag color={v === 'device_revoke' ? 'red' : undefined}>{IDENTITY_ACTION_LABEL[v] ?? v}</Tag>,
  },
  {
    title: '对象',
    key: 'target',
    width: 240,
    ellipsis: true,
    render: (_v, r) => {
      if (r.target_type === 'org') return r.target_id
      if (r.target_type === 'enroll_code') return `${r.org_id} / 注册码 #${r.target_id}`
      return `${r.org_id} / ${r.target_id}`
    },
  },
  { title: '变更', dataIndex: 'detail', ellipsis: true, render: (_v, r) => describeIdentityDetail(r) },
  { title: '理由', dataIndex: 'reason', ellipsis: true, render: (v: string) => v || '—' },
  { title: '操作者', dataIndex: 'actor', width: 140, ellipsis: true },
]
