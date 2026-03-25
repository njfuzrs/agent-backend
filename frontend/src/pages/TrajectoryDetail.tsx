import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Card, Descriptions, Tag, Space, Button, Tabs, Spin, Typography, Rate, Select,
  message,
} from 'antd'
import { ArrowLeftOutlined } from '@ant-design/icons'
import {
  fetchTrajectoryMeta, fetchTrajectorySteps, fetchTrajectoryInfo,
  updateTrajectory,
} from '../services/api'
import Timeline from '../components/Timeline'
import type { TrajectoryMeta } from '../types/trajectory'
import dayjs from 'dayjs'

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

const EXIT_STATUS_MAP: Record<string, { color: string; label: string }> = {
  end_turn: { color: 'success', label: '✅ end_turn' },
  tool_use: { color: 'warning', label: '⚠️ tool_use' },
  error: { color: 'error', label: '❌ error' },
  unknown: { color: 'default', label: 'unknown' },
}

export default function TrajectoryDetail() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState('timeline')

  const { data: meta, isLoading: metaLoading } = useQuery({
    queryKey: ['trajectory-meta', sessionId],
    queryFn: () => fetchTrajectoryMeta(sessionId!),
    enabled: !!sessionId,
  })

  const { data: steps, isLoading: stepsLoading } = useQuery({
    queryKey: ['trajectory-steps', sessionId],
    queryFn: () => fetchTrajectorySteps(sessionId!, 0, 200),
    enabled: !!sessionId && activeTab === 'timeline',
  })

  const { data: info } = useQuery({
    queryKey: ['trajectory-info', sessionId],
    queryFn: () => fetchTrajectoryInfo(sessionId!),
    enabled: !!sessionId,
  })

  if (metaLoading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />
  if (!meta) return <div style={{ color: '#fff' }}>轨迹不存在</div>

  const statusInfo = EXIT_STATUS_MAP[meta.exit_status] || EXIT_STATUS_MAP.unknown

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/trajectories')}>
          返回列表
        </Button>
        <Typography.Text style={{ color: '#999' }}>
          {meta.session_id}
        </Typography.Text>
      </Space>

      <MetaCard meta={meta} statusInfo={statusInfo} />

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        style={{ marginTop: 16 }}
        items={[
          {
            key: 'timeline',
            label: '时间线视图',
            children: stepsLoading ? (
              <Spin style={{ display: 'block', margin: '40px auto' }} />
            ) : (
              <Timeline steps={steps?.items || []} />
            ),
          },
          {
            key: 'info',
            label: '统计信息',
            children: (
              <Card size="small">
                <pre style={{ color: '#d4d4d4', overflow: 'auto', maxHeight: 600 }}>
                  {JSON.stringify(info, null, 2)}
                </pre>
              </Card>
            ),
          },
          {
            key: 'raw',
            label: '下载原始文件',
            children: (
              <Card size="small">
                <Button
                  type="primary"
                  href={`/api/v1/trajectories/${sessionId}/detail/raw`}
                  target="_blank"
                >
                  下载 .traj 文件 ({(meta.traj_file_size / 1024).toFixed(0)} KB)
                </Button>
              </Card>
            ),
          },
        ]}
      />
    </div>
  )
}

function MetaCard({ meta, statusInfo }: { meta: TrajectoryMeta; statusInfo: { color: string; label: string } }) {
  const [rating, setRating] = useState(meta.quality_rating || 0)
  const [qualityStatus, setQualityStatus] = useState(meta.quality_status)

  const handleRatingChange = async (value: number) => {
    setRating(value)
    try {
      await updateTrajectory(meta.session_id, { quality_rating: value })
      message.success('评分已保存')
    } catch {
      message.error('保存失败')
    }
  }

  const handleStatusChange = async (value: string) => {
    setQualityStatus(value)
    try {
      await updateTrajectory(meta.session_id, { quality_status: value })
      message.success('状态已保存')
    } catch {
      message.error('保存失败')
    }
  }

  return (
    <Card size="small">
      <Descriptions column={{ xs: 1, sm: 2, md: 3, lg: 4 }} size="small">
        <Descriptions.Item label="来源">
          <Tag color="blue">{meta.tool_source}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="模型">{meta.model || '-'}</Descriptions.Item>
        <Descriptions.Item label="时间">
          {meta.start_time ? dayjs(meta.start_time).format('YYYY-MM-DD HH:mm:ss') : '-'}
          {meta.end_time && ` ~ ${dayjs(meta.end_time).format('HH:mm:ss')}`}
          {meta.duration_ms && ` (${formatDuration(meta.duration_ms)})`}
        </Descriptions.Item>
        <Descriptions.Item label="状态">
          <Tag color={statusInfo.color}>{statusInfo.label}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="步骤">{meta.total_steps}</Descriptions.Item>
        <Descriptions.Item label="API 调用">{meta.total_api_calls}</Descriptions.Item>
        <Descriptions.Item label="Token">
          {formatTokens(meta.total_tokens)}
          {meta.tokens_sent > 0 && (
            <span style={{ color: '#999', marginLeft: 4 }}>
              (发送 {formatTokens(meta.tokens_sent)} / 接收 {formatTokens(meta.tokens_received)})
            </span>
          )}
        </Descriptions.Item>
        <Descriptions.Item label="成本">
          {meta.total_cost_usd > 0 ? `$${meta.total_cost_usd.toFixed(4)}` : '-'}
        </Descriptions.Item>
        <Descriptions.Item label="工具">
          <Space size={2} wrap>
            {meta.tools_used.map(t => <Tag key={t}>{t}</Tag>)}
          </Space>
        </Descriptions.Item>
        <Descriptions.Item label="工作目录">
          <Typography.Text code style={{ fontSize: 12 }}>
            {meta.working_directory || '-'}
          </Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="Thinking">
          {meta.has_thinking ? <Tag color="purple">有</Tag> : <Tag>无</Tag>}
        </Descriptions.Item>
        <Descriptions.Item label="子代理">
          {meta.has_sub_agent ? <Tag color="cyan">有</Tag> : <Tag>无</Tag>}
        </Descriptions.Item>
      </Descriptions>

      <div style={{ marginTop: 12, borderTop: '1px solid #303030', paddingTop: 12 }}>
        <Space size="large">
          <span>
            质量评分：<Rate value={rating} onChange={handleRatingChange} />
          </span>
          <span>
            状态：
            <Select
              value={qualityStatus}
              onChange={handleStatusChange}
              size="small"
              style={{ width: 120 }}
              options={[
                { value: 'unreviewed', label: '未评审' },
                { value: 'approved', label: '通过' },
                { value: 'rejected', label: '拒绝' },
              ]}
            />
          </span>
        </Space>
      </div>
    </Card>
  )
}
