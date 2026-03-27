import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  Card,
  Descriptions,
  Space,
  Spin,
  Tabs,
  Tag,
  Typography,
} from 'antd'
import { ArrowLeftOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import AiScoreDetail from '../components/AiScoreDetail'
import HistoryChatView from '../components/HistoryChatView'
import QualityRating from '../components/QualityRating'
import RawJsonPane from '../components/RawJsonPane'
import TagManager from '../components/TagManager'
import TrajectoryDebugView from '../components/TrajectoryDebugView'
import TrajectoryReadingView from '../components/TrajectoryReadingView'
import {
  apiBasePath,
  fetchAllTrajectorySteps,
  fetchTrajectoryHistory,
  fetchTrajectoryInfo,
  fetchTrajectoryMeta,
  fetchTrajectoryRawText,
} from '../services/api'
import type { TrajectoryMeta } from '../types/trajectory'
import { buildDetailModel } from '../utils/trajectoryDetail'
import { formatCurrency, formatDuration, formatTokens } from '../utils/format'

const EXIT_STATUS_MAP: Record<string, { color: string; label: string }> = {
  end_turn: { color: 'success', label: '✅ end_turn' },
  tool_use: { color: 'warning', label: '⚠️ tool_use' },
  error: { color: 'error', label: '❌ error' },
  unknown: { color: 'default', label: 'unknown' },
}

export default function TrajectoryDetail() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [activeTab, setActiveTab] = useState('reading')
  const [showSystem, setShowSystem] = useState(false)
  const [showThinking, setShowThinking] = useState(true)
  const [expandTools, setExpandTools] = useState(false)

  const { data: meta, isLoading: metaLoading } = useQuery({
    queryKey: ['trajectory-meta', sessionId],
    queryFn: () => fetchTrajectoryMeta(sessionId!),
    enabled: !!sessionId,
  })

  const shouldLoadDetailData = !!sessionId && ['reading', 'history', 'debug'].includes(activeTab)
  const shouldLoadSteps = !!sessionId && ['reading', 'debug'].includes(activeTab)
  const shouldLoadInfo = !!sessionId && activeTab === 'debug'
  const shouldLoadRaw = !!sessionId && activeTab === 'raw'

  const { data: steps, isLoading: stepsLoading } = useQuery({
    queryKey: ['trajectory-steps', sessionId],
    queryFn: () => fetchAllTrajectorySteps(sessionId!),
    enabled: shouldLoadSteps,
  })

  const { data: history, isLoading: historyLoading } = useQuery({
    queryKey: ['trajectory-history', sessionId],
    queryFn: () => fetchTrajectoryHistory(sessionId!),
    enabled: shouldLoadDetailData,
  })

  const { data: info } = useQuery({
    queryKey: ['trajectory-info', sessionId],
    queryFn: () => fetchTrajectoryInfo(sessionId!),
    enabled: shouldLoadInfo,
  })

  const { data: rawText, isLoading: rawLoading } = useQuery({
    queryKey: ['trajectory-raw-text', sessionId],
    queryFn: () => fetchTrajectoryRawText(sessionId!),
    enabled: shouldLoadRaw,
  })

  if (metaLoading) {
    return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />
  }
  if (!meta) {
    return <div style={{ color: '#fff' }}>轨迹不存在</div>
  }

  const refreshAnnotations = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['trajectory-meta', sessionId] }),
      queryClient.invalidateQueries({ queryKey: ['trajectories'] }),
    ])
  }

  const statusInfo = EXIT_STATUS_MAP[meta.exit_status] || EXIT_STATUS_MAP.unknown
  const detailModel = buildDetailModel(history || [], steps?.items || [], meta.first_prompt || '')
  const readingLoading = (shouldLoadSteps && stepsLoading) || historyLoading

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
        <Space style={{ marginBottom: 4 }}>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/trajectories')}>
            返回列表
          </Button>
          <Typography.Text style={{ color: '#999' }}>{meta.session_id}</Typography.Text>
        </Space>

        <Space wrap>
          <Tag>步骤 {meta.total_steps}</Tag>
          <Tag color="blue">工具 {meta.tools_used.length}</Tag>
          <Button
            type="primary"
            href={`${apiBasePath}/trajectories/${sessionId}/detail/raw`}
            target="_blank"
          >
            下载 .traj
          </Button>
        </Space>
      </div>

      <MetaCard meta={meta} statusInfo={statusInfo} />

      <AiScoreDetail sessionId={sessionId!} />

      <Card title="质量标注" size="small">
        <div style={{ display: 'grid', gap: 16 }}>
          <QualityRating meta={meta} onUpdated={refreshAnnotations} />
          <div>
            <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>
              标签管理
            </Typography.Text>
            <TagManager sessionId={meta.session_id} tags={meta.tags} onUpdated={refreshAnnotations} />
          </div>
        </div>
      </Card>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: 'reading',
            label: '阅读视图',
            children: readingLoading ? (
              <Spin style={{ display: 'block', margin: '40px auto' }} />
            ) : (
              <TrajectoryReadingView
                model={detailModel}
                showSystem={showSystem}
                onShowSystemChange={setShowSystem}
                showThinking={showThinking}
                onShowThinkingChange={setShowThinking}
                expandTools={expandTools}
                onExpandToolsChange={setExpandTools}
              />
            ),
          },
          {
            key: 'history',
            label: 'History 视图',
            children: historyLoading ? (
              <Spin style={{ display: 'block', margin: '40px auto' }} />
            ) : (
              <HistoryChatView history={history || []} />
            ),
          },
          {
            key: 'debug',
            label: '调试视图',
            children: shouldLoadSteps && stepsLoading ? (
              <Spin style={{ display: 'block', margin: '40px auto' }} />
            ) : (
              <TrajectoryDebugView steps={steps?.items || []} history={history || []} info={info} />
            ),
          },
          {
            key: 'raw',
            label: '原始 JSON',
            children: rawLoading ? (
              <Spin style={{ display: 'block', margin: '40px auto' }} />
            ) : (
              <RawJsonPane content={rawText || ''} />
            ),
          },
        ]}
      />
    </div>
  )
}

function MetaCard({
  meta,
  statusInfo,
}: {
  meta: TrajectoryMeta
  statusInfo: { color: string; label: string }
}) {
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
          {meta.tokens_sent > 0 ? (
            <span style={{ color: '#999', marginLeft: 4 }}>
              (发送 {formatTokens(meta.tokens_sent)} / 接收 {formatTokens(meta.tokens_received)})
            </span>
          ) : null}
        </Descriptions.Item>
        <Descriptions.Item label="成本">{formatCurrency(meta.total_cost_usd)}</Descriptions.Item>
        <Descriptions.Item label="工具">
          <Space size={2} wrap>
            {meta.tools_used.map(tool => <Tag key={tool}>{tool}</Tag>)}
          </Space>
        </Descriptions.Item>
        <Descriptions.Item label="任务类型">
          {meta.task_type ? <Tag color="geekblue">{meta.task_type}</Tag> : '-'}
        </Descriptions.Item>
        <Descriptions.Item label="项目名">{meta.project_name || '-'}</Descriptions.Item>
        <Descriptions.Item label="工作目录">
          <Typography.Text code style={{ fontSize: 12 }}>
            {meta.working_directory || '-'}
          </Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="首条输入" span={4}>
          <Typography.Text style={{ color: '#d9d9d9', whiteSpace: 'pre-wrap' }}>
            {meta.first_prompt || '-'}
          </Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="Thinking">
          {meta.has_thinking ? <Tag color="purple">有</Tag> : <Tag>无</Tag>}
        </Descriptions.Item>
        <Descriptions.Item label="子代理">
          {meta.has_sub_agent ? <Tag color="cyan">有</Tag> : <Tag>无</Tag>}
        </Descriptions.Item>
      </Descriptions>
    </Card>
  )
}
