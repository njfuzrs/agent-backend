import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Alert, Card, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import { fetchPluginUsage } from '../services/api'
import type { PluginUsageItem, PluginUserUsage } from '../types/event'

const DAYS = 30

// 一行 = 插件 × 人。插件级汇总放在每个插件的第一行，用 rowSpan 合并。
type Row = PluginUserUsage & {
  key: string
  plugin: PluginUsageItem
  span: number
}

function personLabel(row: PluginUserUsage) {
  if (row.user_ref === null) return <Typography.Text type="secondary">未登录设备（合计）</Typography.Text>
  return (
    <span>
      {row.name || `user#${row.user_ref}`}
      {row.union_id && (
        <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 6 }}>
          {row.union_id}
        </Typography.Text>
      )}
    </span>
  )
}

export default function PluginUsageCard() {
  const { data, isLoading } = useQuery({
    queryKey: ['event-plugin-usage', DAYS],
    queryFn: () => fetchPluginUsage({ days: DAYS }),
  })

  const rows = useMemo<Row[]>(() => {
    const out: Row[] = []
    for (const plugin of data?.items ?? []) {
      plugin.by_user.forEach((u, i) => {
        out.push({
          ...u,
          key: `${plugin.plugin_name}-${u.user_ref ?? 'anon'}`,
          plugin,
          span: i === 0 ? plugin.by_user.length : 0,
        })
      })
    }
    return out
  }, [data])

  const columns: ColumnsType<Row> = [
    {
      title: '插件',
      key: 'plugin',
      onCell: row => ({ rowSpan: row.span }),
      render: (_, row) => (
        <span>
          <Typography.Text code>{row.plugin.plugin_name}</Typography.Text>
          {row.plugin.marketplaces.map(m => (
            <Tag key={m} style={{ marginLeft: 6 }}>
              {m}
            </Tag>
          ))}
        </span>
      ),
    },
    {
      title: '插件合计',
      key: 'total',
      width: 200,
      onCell: row => ({ rowSpan: row.span }),
      render: (_, row) => (
        <Typography.Text>
          {row.plugin.calls} 次 · {row.plugin.users} 人{row.plugin.has_anonymous ? ' + 未登录' : ''}
          <br />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            MCP {row.plugin.mcp_calls} / Skill {row.plugin.skill_calls}
          </Typography.Text>
        </Typography.Text>
      ),
    },
    { title: '人', key: 'person', render: (_, row) => personLabel(row) },
    { title: '调用次数', dataIndex: 'calls', width: 100, align: 'right' },
    {
      title: '最近一次',
      dataIndex: 'last_received_at',
      width: 170,
      render: (v: string | null) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm') : '—'),
    },
  ]

  return (
    <Card size="small" title={`插件调用（近 ${DAYS} 天）`}>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        数据来自 <Typography.Text code>tool_invoked</Typography.Text> 事件：客户端只对企业市场安装的插件上报，不含参数。
        归属看上报设备当时绑定的人；未登录设备无法区分是几个人，合成一行、不计入人数。
        需 sid-code 支持该事件的版本，旧客户端的调用不会出现在这里。
      </Typography.Paragraph>
      {data?.truncated && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={`窗口内事件超过扫描上限，只统计了最新的 ${data.scanned} 条，数字是下限`}
        />
      )}
      <Table
        rowKey="key"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={rows}
        pagination={false}
        locale={{ emptyText: '近 30 天没有企业市场插件的调用记录' }}
      />
    </Card>
  )
}
