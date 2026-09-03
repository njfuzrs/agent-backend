import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Card, Col, Row, Segmented, Select, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import StatsCard from '../components/StatsCard'
import {
  DonutChart,
  HorizontalBarChart,
  LineTrendChart,
  StackedAreaChart,
} from '../components/SimpleCharts'
import {
  fetchCostStats,
  fetchModelDistribution,
  fetchScoringStats,
  fetchStatsOverview,
  fetchStatsTrends,
  fetchToolDistribution,
  fetchTrajectories,
} from '../services/api'
import type { TrajectoryListItem, ScoringStatsResponse } from '../types/trajectory'
import { CHART_COLORS } from '../../../utils/chart'
import { formatCurrency, formatPercent, formatTokens } from '../../../utils/format'

const TOOL_SOURCE_COLORS: Record<string, string> = {
  'claude-code': 'blue',
  codex: 'green',
  'gemini-cli': 'orange',
  'sid-code': 'purple',
}

function formatDateLabel(value: string) {
  return dayjs(value).format('MM-DD')
}

export default function Dashboard() {
  const navigate = useNavigate()
  const [granularity, setGranularity] = useState<'day' | 'week'>('day')
  const [toolSource, setToolSource] = useState<string | undefined>(undefined)

  const filters = toolSource ? { tool_source: toolSource } : {}

  const { data: overview } = useQuery({
    queryKey: ['stats-overview', filters],
    queryFn: () => fetchStatsOverview(filters),
  })

  const { data: trends } = useQuery({
    queryKey: ['stats-trends', granularity, filters],
    queryFn: () => fetchStatsTrends({ granularity, ...filters }),
  })

  const { data: tools } = useQuery({
    queryKey: ['stats-tools', filters],
    queryFn: () => fetchToolDistribution({ limit: 8, ...filters }),
  })

  const { data: models } = useQuery({
    queryKey: ['stats-models', filters],
    queryFn: () => fetchModelDistribution({ limit: 6, ...filters }),
  })

  const { data: cost } = useQuery({
    queryKey: ['stats-cost', granularity, filters],
    queryFn: () => fetchCostStats({ granularity, ...filters }),
  })

  const { data: recent, isLoading: recentLoading } = useQuery({
    queryKey: ['dashboard-recent', filters],
    queryFn: () => fetchTrajectories({ page: 1, page_size: 10, sort: '-start_time', ...filters }),
  })

  const { data: scoringStats } = useQuery<ScoringStatsResponse>({
    queryKey: ['scoring-stats'],
    queryFn: fetchScoringStats,
  })

  const columns: ColumnsType<TrajectoryListItem> = [
    {
      title: '时间',
      dataIndex: 'start_time',
      width: 120,
      render: (value: string | null) => value ? dayjs(value).format('MM-DD HH:mm') : '-',
    },
    {
      title: '来源',
      dataIndex: 'tool_source',
      width: 110,
      render: (value: string) => <Tag color={TOOL_SOURCE_COLORS[value] || 'default'}>{value}</Tag>,
    },
    {
      title: '模型',
      dataIndex: 'model',
      ellipsis: true,
    },
    {
      title: '步骤',
      dataIndex: 'total_steps',
      width: 80,
      align: 'right',
    },
    {
      title: '成本',
      dataIndex: 'total_cost_usd',
      width: 90,
      align: 'right',
      render: (value: number) => formatCurrency(value),
    },
    {
      title: '状态',
      dataIndex: 'quality_status',
      width: 110,
      render: (value: string) => <Tag color={value === 'approved' ? 'success' : value === 'rejected' ? 'error' : 'default'}>{value}</Tag>,
    },
  ]

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
        <div>
          <Typography.Title level={4} style={{ color: '#fff', margin: 0 }}>
            统计仪表盘
          </Typography.Title>
          <Typography.Text style={{ color: '#8c8c8c' }}>
            全局概览、趋势分析与最近轨迹预览
          </Typography.Text>
        </div>

        <Space wrap>
          <Segmented<'day' | 'week'>
            value={granularity}
            onChange={value => setGranularity(value)}
            options={[
              { label: '按天', value: 'day' },
              { label: '按周', value: 'week' },
            ]}
          />
          <Select
            allowClear
            placeholder="全部来源"
            style={{ width: 160 }}
            value={toolSource}
            onChange={value => setToolSource(value)}
            options={[
              { value: 'claude-code', label: 'Claude Code' },
              { value: 'codex', label: 'Codex' },
              { value: 'gemini-cli', label: 'Gemini CLI' },
              { value: 'sid-code', label: 'sid-code' },
            ]}
          />
        </Space>
      </div>

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <StatsCard
            title="总轨迹数"
            value={String(overview?.total_trajectories ?? 0)}
            hint={`平均步骤 ${overview?.avg_steps_per_trajectory?.toFixed(1) ?? '0.0'}`}
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatsCard
            title="总 Token"
            value={formatTokens(overview?.total_tokens ?? 0)}
            hint={`成功率 ${formatPercent(overview?.success_rate ?? 0)}`}
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatsCard
            title="总成本"
            value={formatCurrency(overview?.total_cost_usd ?? 0)}
            hint={`单条平均 ${formatCurrency(overview?.avg_cost_per_trajectory ?? 0)}`}
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatsCard
            title="评审分布"
            value={`${overview?.quality_distribution?.approved ?? 0} 条通过`}
            hint={`未评审 ${overview?.quality_distribution?.unreviewed ?? 0} 条`}
          />
        </Col>
      </Row>

      {/* AI 评分概览 */}
      {scoringStats && (
        <Row gutter={[16, 16]}>
          <Col xs={24} sm={12} lg={6}>
            <StatsCard
              title="AI 已评分"
              value={String(scoringStats.total_scored)}
              hint={`待评分 ${scoringStats.total_pending} 条`}
            />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <StatsCard
              title="AI 平均分"
              value={String(scoringStats.avg_score)}
              hint={`通过 ${scoringStats.status_distribution?.auto_approved ?? 0} 条`}
            />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <StatsCard
              title="AI 通过率"
              value={formatPercent(
                scoringStats.total_scored > 0
                  ? (scoringStats.status_distribution?.auto_approved ?? 0) / scoringStats.total_scored
                  : 0
              )}
              hint={`拒绝 ${scoringStats.status_distribution?.auto_rejected ?? 0} 条`}
            />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <StatsCard
              title="AI 等级分布"
              value={`A:${scoringStats.grade_distribution?.A ?? 0} B:${scoringStats.grade_distribution?.B ?? 0}`}
              hint={`C:${scoringStats.grade_distribution?.C ?? 0} D:${scoringStats.grade_distribution?.D ?? 0} F:${scoringStats.grade_distribution?.F ?? 0}`}
            />
          </Col>
        </Row>
      )}

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={14}>
          <Card title="Token 消耗趋势" size="small">
            <LineTrendChart
              color={CHART_COLORS[0]}
              data={(trends?.data ?? []).map(item => ({
                label: formatDateLabel(item.date),
                value: item.total_tokens,
              }))}
            />
          </Card>
        </Col>
        <Col xs={24} xl={10}>
          <Card title="工具来源分布" size="small">
            <DonutChart
              items={Object.entries(overview?.tool_source_distribution ?? {}).map(([name, count]) => ({
                name,
                count,
              }))}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={12}>
          <Card title="工具使用频率" size="small">
            <HorizontalBarChart items={tools?.items ?? []} />
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card title="模型使用分布" size="small">
            <DonutChart
              items={(models?.items ?? []).map(item => ({
                name: item.name,
                count: item.count,
              }))}
            />
          </Card>
        </Col>
      </Row>

      <Card title="成本趋势" size="small">
        <StackedAreaChart
          dates={(cost?.dates ?? []).map(formatDateLabel)}
          series={cost?.series ?? []}
        />
      </Card>

      <Card title="最近轨迹" size="small">
        <Table
          rowKey="session_id"
          loading={recentLoading}
          columns={columns}
          dataSource={recent?.items ?? []}
          pagination={false}
          size="small"
          onRow={record => ({
            onClick: () => navigate(`/trajectories/${record.session_id}`),
            style: { cursor: 'pointer' },
          })}
          scroll={{ x: 760 }}
        />
      </Card>
    </div>
  )
}
