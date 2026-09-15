import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  Card,
  Descriptions,
  Empty,
  Popconfirm,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { ArrowLeftOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import RadarChart from '../components/RadarChart'
import ToolUsageCompareChart from '../components/ToolUsageCompareChart'
import {
  fetchCompareGroupDetail,
  fetchCompareRadar,
  removeCompareGroupItem,
} from '../services/api'
import type { CompareTrajectoryItem } from '../types/trajectory'
import { formatCurrency, formatDuration, formatTokens } from '../../../utils/format'

const EXIT_STATUS_TAG: Record<string, { color: string; label: string }> = {
  end_turn: { color: 'success', label: 'end_turn' },
  tool_use: { color: 'warning', label: 'tool_use' },
  error: { color: 'error', label: 'error' },
  unknown: { color: 'default', label: 'unknown' },
}

interface MetricRow {
  key: string
  label: string
  values: Record<number, string | number>
  bestTrajectoryIds: number[]
}

export default function CompareDetail() {
  const { groupId } = useParams<{ groupId: string }>()
  const groupIdNumber = Number(groupId)
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ['compare-group-detail', groupIdNumber],
    queryFn: () => fetchCompareGroupDetail(groupIdNumber),
    enabled: Number.isFinite(groupIdNumber),
  })

  const { data: radar, isLoading: radarLoading } = useQuery({
    queryKey: ['compare-radar', groupIdNumber],
    queryFn: () => fetchCompareRadar(groupIdNumber),
    enabled: Number.isFinite(groupIdNumber),
  })

  const removeMutation = useMutation({
    mutationFn: ({ trajectoryId }: { trajectoryId: number }) =>
      removeCompareGroupItem(groupIdNumber, trajectoryId),
    onSuccess: async () => {
      message.success('已从对比组移除')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['compare-group-detail', groupIdNumber] }),
        queryClient.invalidateQueries({ queryKey: ['compare-radar', groupIdNumber] }),
        queryClient.invalidateQueries({ queryKey: ['compare-groups'] }),
      ])
    },
    onError: () => {
      message.error('移除失败')
    },
  })

  const detailItems = detail?.items ?? []
  const metricRows = detailItems.length ? [
    buildMetricRow(detailItems, 'total_steps', '步骤数', 'min'),
    buildMetricRow(detailItems, 'total_tokens', 'Token', 'min'),
    buildMetricRow(detailItems, 'duration_ms', '耗时', 'min'),
    buildMetricRow(detailItems, 'total_cost_usd', '成本', 'min'),
    buildMetricRow(detailItems, 'exit_status', '退出状态', 'status'),
    buildMetricRow(detailItems, 'tools_used', '工具种类', 'max'),
  ] : []

  const toolUsageCategories = Array.from(
    new Set(detailItems.flatMap(item => Object.keys(item.tool_usage)))
  ).sort((left, right) => {
    const leftTotal = detailItems.reduce((sum, item) => sum + (item.tool_usage[left] ?? 0), 0)
    const rightTotal = detailItems.reduce((sum, item) => sum + (item.tool_usage[right] ?? 0), 0)
    return rightTotal - leftTotal
  })

  const toolUsageSeries = detailItems.map(item => ({
    name: buildSeriesName(item),
    values: toolUsageCategories.map(category => item.tool_usage[category] ?? 0),
  }))

  const metricColumns: ColumnsType<MetricRow> = [
    {
      title: '指标',
      dataIndex: 'label',
      fixed: 'left',
      width: 120,
    },
    ...((detail?.items ?? []).map(item => ({
      title: (
        <div style={{ display: 'grid', gap: 4 }}>
          <span>{buildSeriesName(item)}</span>
          <Typography.Text style={{ color: '#8c8c8c', fontSize: 12 }}>
            {item.session_id.slice(0, 8)}
          </Typography.Text>
        </div>
      ),
      key: String(item.trajectory_id),
      width: 160,
      render: (_value: unknown, row: MetricRow) => {
        const value = row.values[item.trajectory_id]
        const highlighted = row.bestTrajectoryIds.includes(item.trajectory_id)
        return (
          <span style={{ color: highlighted ? '#95de64' : '#d9d9d9', fontWeight: highlighted ? 700 : 400 }}>
            {String(value)}
          </span>
        )
      },
    })) as ColumnsType<MetricRow>),
  ]

  if (detailLoading) {
    return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />
  }

  if (!detail) {
    return <Empty description="对比组不存在" />
  }

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <Space>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/compare')}>
          返回对比组
        </Button>
        <Typography.Text style={{ color: '#8c8c8c' }}>
          对比组 #{detail.id}
        </Typography.Text>
      </Space>

      <Card size="small">
        <Descriptions column={{ xs: 1, sm: 2, lg: 4 }} size="small">
          <Descriptions.Item label="名称">{detail.name}</Descriptions.Item>
          <Descriptions.Item label="轨迹数">{detail.item_count}</Descriptions.Item>
          <Descriptions.Item label="创建时间">
            {dayjs(detail.created_at).format('YYYY-MM-DD HH:mm:ss')}
          </Descriptions.Item>
          <Descriptions.Item label="任务提示词">
            {detail.task_prompt || '-'}
          </Descriptions.Item>
          <Descriptions.Item label="说明" span={4}>
            {detail.description || '-'}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="对比项" size="small">
        {detail.items.length ? (
          <div style={{ display: 'grid', gap: 12 }}>
            {detail.items.map(item => {
              const status = EXIT_STATUS_TAG[item.exit_status] || EXIT_STATUS_TAG.unknown
              return (
                <div
                  key={item.trajectory_id}
                  style={{
                    display: 'grid',
                    gridTemplateColumns: '1.6fr 1fr auto',
                    gap: 16,
                    alignItems: 'center',
                    border: '1px solid #2a2a2a',
                    borderRadius: 12,
                    padding: 14,
                  }}
                >
                  <div style={{ minWidth: 0 }}>
                    <Space wrap size={[8, 8]}>
                      <Tag color="blue">{item.tool_source}</Tag>
                      <Tag>{item.model || 'unknown'}</Tag>
                      <Tag color={status.color}>{status.label}</Tag>
                      <Tag color={item.quality_status === 'approved' ? 'success' : item.quality_status === 'rejected' ? 'error' : 'default'}>
                        {item.quality_status}
                      </Tag>
                    </Space>
                    <Typography.Paragraph
                      style={{ color: '#d9d9d9', margin: '8px 0 0 0' }}
                      ellipsis={{ rows: 2 }}
                    >
                      {item.first_prompt || item.session_id}
                    </Typography.Paragraph>
                  </div>

                  <div style={{ display: 'grid', gap: 4 }}>
                    <Typography.Text style={{ color: '#8c8c8c' }}>Session</Typography.Text>
                    <Typography.Text copyable style={{ color: '#fff' }}>
                      {item.session_id}
                    </Typography.Text>
                    <Typography.Text style={{ color: '#8c8c8c' }}>
                      {item.start_time ? dayjs(item.start_time).format('MM-DD HH:mm') : '-'} · {formatDuration(item.duration_ms)}
                    </Typography.Text>
                  </div>

                  <Space wrap>
                    <Button type="link">
                      <Link to={`/trajectories/${item.session_id}`}>查看详情</Link>
                    </Button>
                    <Popconfirm
                      title="将该轨迹移出对比组？"
                      onConfirm={() => removeMutation.mutate({ trajectoryId: item.trajectory_id })}
                      okButtonProps={{ loading: removeMutation.isPending }}
                    >
                      <Button type="link" danger>
                        移除
                      </Button>
                    </Popconfirm>
                  </Space>
                </div>
              )
            })}
          </div>
        ) : (
          <Empty description="对比组暂无轨迹，请从轨迹列表添加" />
        )}
      </Card>

      <Card title="雷达图对比" size="small">
        {radarLoading ? (
          <Spin style={{ display: 'block', margin: '48px auto' }} />
        ) : (
          <RadarChart
            dimensions={radar?.dimensions ?? []}
            items={(radar?.items ?? []).map(item => ({
              name: `${item.tool_source} · ${item.model || 'unknown'}`,
              values: item.normalized,
            }))}
          />
        )}
      </Card>

      <Card title="指标对比" size="small">
        <Table
          rowKey="key"
          columns={metricColumns}
          dataSource={metricRows}
          pagination={false}
          size="small"
          scroll={{ x: 820 }}
        />
      </Card>

      <Card title="工具使用对比" size="small">
        <ToolUsageCompareChart categories={toolUsageCategories} series={toolUsageSeries} />
      </Card>
    </div>
  )
}


function buildSeriesName(item: CompareTrajectoryItem) {
  return `${item.tool_source} · ${item.model || 'unknown'}`
}

function buildMetricRow(
  items: CompareTrajectoryItem[],
  key: 'total_steps' | 'total_tokens' | 'duration_ms' | 'total_cost_usd' | 'exit_status' | 'tools_used',
  label: string,
  strategy: 'min' | 'max' | 'status'
): MetricRow {
  const numericValues = items.map(item => {
    if (key === 'tools_used') return item.tools_used.length
    if (key === 'duration_ms') return item.duration_ms ?? 0
    if (key === 'exit_status') return item.exit_status === 'end_turn' ? 1 : 0
    return Number(item[key] ?? 0)
  })

  let bestValue = numericValues[0] ?? 0
  if (strategy === 'min') {
    bestValue = Math.min(...numericValues)
  } else if (strategy === 'max' || strategy === 'status') {
    bestValue = Math.max(...numericValues)
  }

  const values = Object.fromEntries(items.map(item => {
    let displayValue: string | number
    if (key === 'total_tokens') {
      displayValue = formatTokens(item.total_tokens)
    } else if (key === 'duration_ms') {
      displayValue = formatDuration(item.duration_ms)
    } else if (key === 'total_cost_usd') {
      displayValue = formatCurrency(item.total_cost_usd)
    } else if (key === 'exit_status') {
      displayValue = item.exit_status
    } else if (key === 'tools_used') {
      displayValue = item.tools_used.length
    } else {
      displayValue = item[key]
    }
    return [item.trajectory_id, displayValue]
  }))

  return {
    key,
    label,
    values,
    bestTrajectoryIds: items
      .filter(item => {
        const value =
          key === 'tools_used' ? item.tools_used.length :
            key === 'duration_ms' ? item.duration_ms ?? 0 :
              key === 'exit_status' ? (item.exit_status === 'end_turn' ? 1 : 0) :
                Number(item[key] ?? 0)
        return value === bestValue
      })
      .map(item => item.trajectory_id),
  }
}
