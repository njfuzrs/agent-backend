import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Table, Input, Select, Space, Tag, DatePicker, Card, Typography, Tooltip,
} from 'antd'
import {
  CheckCircleOutlined, WarningOutlined, CloseCircleOutlined,
  SearchOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import { fetchTrajectories } from '../services/api'
import type { TrajectoryListItem } from '../types/trajectory'

const { RangePicker } = DatePicker

const TOOL_SOURCE_COLORS: Record<string, string> = {
  'claude-code': 'blue',
  codex: 'green',
  'gemini-cli': 'orange',
  'sid-code': 'purple',
}

const EXIT_STATUS_ICON: Record<string, React.ReactNode> = {
  end_turn: <CheckCircleOutlined style={{ color: '#52c41a' }} />,
  tool_use: <WarningOutlined style={{ color: '#faad14' }} />,
  error: <CloseCircleOutlined style={{ color: '#ff4d4f' }} />,
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`
  return String(n)
}

function formatDuration(ms: number | null): string {
  if (!ms) return '-'
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m${s % 60}s`
}

export default function TrajectoryList() {
  const navigate = useNavigate()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [filters, setFilters] = useState<Record<string, unknown>>({})
  const [sort, setSort] = useState('-start_time')

  const queryParams = { page, page_size: pageSize, sort, ...filters }

  const { data, isLoading } = useQuery({
    queryKey: ['trajectories', queryParams],
    queryFn: () => fetchTrajectories(queryParams),
  })

  const columns: ColumnsType<TrajectoryListItem> = [
    {
      title: '时间',
      dataIndex: 'start_time',
      width: 130,
      sorter: true,
      render: (v: string) => v ? dayjs(v).format('MM-DD HH:mm') : '-',
    },
    {
      title: '来源',
      dataIndex: 'tool_source',
      width: 110,
      render: (v: string) => <Tag color={TOOL_SOURCE_COLORS[v] || 'default'}>{v}</Tag>,
    },
    {
      title: '模型',
      dataIndex: 'model',
      width: 140,
      ellipsis: true,
      render: (v: string) => v?.replace('claude-', '') || '-',
    },
    {
      title: '首条输入',
      dataIndex: 'first_prompt',
      ellipsis: true,
      render: (v: string) => (
        <Tooltip title={v} placement="topLeft">
          <span>{v?.slice(0, 80) || '-'}</span>
        </Tooltip>
      ),
    },
    {
      title: '步骤',
      dataIndex: 'total_steps',
      width: 70,
      sorter: true,
      align: 'right',
    },
    {
      title: 'Token',
      dataIndex: 'total_tokens',
      width: 80,
      sorter: true,
      align: 'right',
      render: (v: number) => formatTokens(v),
    },
    {
      title: '成本',
      dataIndex: 'total_cost_usd',
      width: 80,
      sorter: true,
      align: 'right',
      render: (v: number) => v > 0 ? `$${v.toFixed(2)}` : '-',
    },
    {
      title: '耗时',
      dataIndex: 'duration_ms',
      width: 80,
      align: 'right',
      render: (v: number | null) => formatDuration(v),
    },
    {
      title: '状态',
      dataIndex: 'exit_status',
      width: 70,
      align: 'center',
      render: (v: string) => EXIT_STATUS_ICON[v] || <span>{v || '-'}</span>,
    },
    {
      title: '用户',
      dataIndex: 'user_id',
      width: 100,
      ellipsis: true,
      render: (v: string | null) => v || '-',
    },
  ]

  const handleTableChange = (pagination: any, _filters: any, sorter: any) => {
    setPage(pagination.current)
    setPageSize(pagination.pageSize)
    if (sorter.field) {
      const dir = sorter.order === 'ascend' ? '' : '-'
      setSort(`${dir}${sorter.field}`)
    }
  }

  return (
    <div>
      <Typography.Title level={4} style={{ color: '#fff', marginBottom: 16 }}>
        轨迹列表
      </Typography.Title>

      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap size="middle">
          <Input
            placeholder="搜索用户输入..."
            prefix={<SearchOutlined />}
            allowClear
            style={{ width: 260 }}
            onChange={e => {
              const v = e.target.value
              setFilters(f => v ? { ...f, search: v } : (() => { const { search, ...rest } = f as any; return rest })())
              setPage(1)
            }}
          />
          <Select
            placeholder="工具来源"
            allowClear
            style={{ width: 140 }}
            options={[
              { value: 'claude-code', label: 'Claude Code' },
              { value: 'codex', label: 'Codex' },
              { value: 'gemini-cli', label: 'Gemini CLI' },
              { value: 'sid-code', label: 'sid-code' },
            ]}
            onChange={v => {
              setFilters(f => v ? { ...f, tool_source: v } : (() => { const { tool_source, ...rest } = f as any; return rest })())
              setPage(1)
            }}
          />
          <Select
            placeholder="退出状态"
            allowClear
            style={{ width: 130 }}
            options={[
              { value: 'end_turn', label: '✅ end_turn' },
              { value: 'tool_use', label: '⚠️ tool_use' },
              { value: 'error', label: '❌ error' },
              { value: 'unknown', label: 'unknown' },
            ]}
            onChange={v => {
              setFilters(f => v ? { ...f, exit_status: v } : (() => { const { exit_status, ...rest } = f as any; return rest })())
              setPage(1)
            }}
          />
          <RangePicker
            onChange={dates => {
              if (dates && dates[0] && dates[1]) {
                setFilters(f => ({
                  ...f,
                  start_date: dates[0]!.format('YYYY-MM-DD'),
                  end_date: dates[1]!.format('YYYY-MM-DD'),
                }))
              } else {
                setFilters(f => {
                  const { start_date, end_date, ...rest } = f as any
                  return rest
                })
              }
              setPage(1)
            }}
          />
        </Space>
      </Card>

      <Table
        rowKey="session_id"
        columns={columns}
        dataSource={data?.items || []}
        loading={isLoading}
        pagination={{
          current: page,
          pageSize,
          total: data?.total || 0,
          showSizeChanger: true,
          showTotal: total => `共 ${total} 条`,
        }}
        onChange={handleTableChange}
        onRow={record => ({
          onClick: () => navigate(`/trajectories/${record.session_id}`),
          style: { cursor: 'pointer' },
        })}
        size="small"
        scroll={{ x: 1200 }}
      />
    </div>
  )
}
