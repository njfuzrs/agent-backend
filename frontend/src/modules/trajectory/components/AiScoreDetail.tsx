import { Button, Card, Descriptions, Progress, Space, Tag, Typography, message } from 'antd'
import { ReloadOutlined, RobotOutlined } from '@ant-design/icons'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchScoreDetail, scoreTrajectory } from '../services/api'
import type { ScoringDetail } from '../types/trajectory'

const RULE_LABELS: Record<string, string> = {
  R01: '完成状态',
  R02: '步骤数',
  R03: '工具多样性',
  R04: '内容质量',
  R05: 'Token 效率',
  R06: '思考过程',
  R07: '成本合理',
  R08: '时长合理',
  R09: '文件编辑',
  R10: '任务类型',
}

const HEURISTIC_LABELS: Record<string, string> = {
  H01: '重复检测',
  H02: '工具链',
  H03: '错误恢复',
  H04: '步骤效率',
  H05: '编辑验证',
  H06: '搜索先行',
}

const GRADE_COLORS: Record<string, string> = {
  A: '#52c41a',
  B: '#73d13d',
  C: '#faad14',
  D: '#ff7a45',
  F: '#ff4d4f',
}

const STATUS_MAP: Record<string, { color: string; label: string }> = {
  auto_approved: { color: 'success', label: 'AI 通过' },
  auto_rejected: { color: 'error', label: 'AI 拒绝' },
  needs_review: { color: 'warning', label: '待复核' },
  pending: { color: 'default', label: '待评分' },
  error: { color: 'default', label: '评分异常' },
}

function ScoreBar({ label, score }: { label: string; score: number }) {
  const color = score >= 80 ? '#52c41a' : score >= 60 ? '#73d13d' : score >= 40 ? '#faad14' : '#ff4d4f'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 2 }}>
      <span style={{ width: 72, fontSize: 12, color: '#b0b0b0', flexShrink: 0 }}>{label}</span>
      <Progress
        percent={score}
        size="small"
        strokeColor={color}
        style={{ flex: 1, margin: 0 }}
        format={p => <span style={{ fontSize: 11, color: '#b0b0b0' }}>{p}</span>}
      />
    </div>
  )
}

export default function AiScoreDetail({ sessionId }: { sessionId: string }) {
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery<ScoringDetail>({
    queryKey: ['scoring', sessionId],
    queryFn: () => fetchScoreDetail(sessionId),
  })

  const scoreMutation = useMutation({
    mutationFn: (params: { heuristic: boolean; llm: boolean }) =>
      scoreTrajectory(sessionId, params.heuristic, params.llm),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['scoring', sessionId] })
      queryClient.invalidateQueries({ queryKey: ['trajectory-meta', sessionId] })
      message.success('评分完成')
    },
    onError: () => message.error('评分失败'),
  })

  if (isLoading) return null

  const d = data!
  const status = STATUS_MAP[d.ai_quality_status] || STATUS_MAP.pending
  const hasRule = d.rule_score != null
  const hasHeuristic = d.heuristic_score != null
  const hasLlm = d.llm_score != null

  return (
    <Card
      size="small"
      title={
        <Space>
          <RobotOutlined />
          <span>AI 评分</span>
          {d.ai_score != null && (
            <Tag color={GRADE_COLORS[d.ai_grade] ? undefined : 'default'} style={{ color: GRADE_COLORS[d.ai_grade] || undefined, borderColor: GRADE_COLORS[d.ai_grade] || undefined }}>
              {d.ai_grade} · {d.ai_score} 分
            </Tag>
          )}
          <Tag color={status.color}>{status.label}</Tag>
        </Space>
      }
      extra={
        <Space>
          <Button
            size="small"
            icon={<ReloadOutlined />}
            loading={scoreMutation.isPending}
            onClick={() => scoreMutation.mutate({ heuristic: true, llm: false })}
          >
            重新评分
          </Button>
          <Button
            size="small"
            loading={scoreMutation.isPending}
            onClick={() => scoreMutation.mutate({ heuristic: true, llm: true })}
          >
            LLM 深度评估
          </Button>
        </Space>
      }
    >
      {/* 三层分数概览 */}
      <Descriptions column={{ xs: 1, sm: 3 }} size="small" bordered>
        <Descriptions.Item label="规则评分">
          {hasRule ? <Progress percent={d.rule_score!} size="small" steps={5} /> : <Tag>未执行</Tag>}
        </Descriptions.Item>
        <Descriptions.Item label="启发式评分">
          {hasHeuristic ? <Progress percent={d.heuristic_score!} size="small" steps={5} /> : <Tag>未执行</Tag>}
        </Descriptions.Item>
        <Descriptions.Item label="LLM 评分">
          {hasLlm ? <Progress percent={d.llm_score!} size="small" steps={5} /> : <Tag>未执行</Tag>}
        </Descriptions.Item>
      </Descriptions>

      {/* 规则细项 */}
      {hasRule && Object.keys(d.rule_details).length > 0 && (
        <Card type="inner" title="规则评分细项" size="small" style={{ marginTop: 12 }}>
          {Object.entries(d.rule_details).map(([key, val]) => (
            <ScoreBar key={key} label={RULE_LABELS[key] || key} score={val} />
          ))}
        </Card>
      )}

      {/* 启发式细项 */}
      {hasHeuristic && Object.keys(d.heuristic_details).length > 0 && (
        <Card type="inner" title="启发式分析细项" size="small" style={{ marginTop: 12 }}>
          {Object.entries(d.heuristic_details).map(([key, val]) => (
            <ScoreBar key={key} label={HEURISTIC_LABELS[key] || key} score={val} />
          ))}
          {d.heuristic_patterns.length > 0 && (
            <div style={{ marginTop: 8 }}>
              {d.heuristic_patterns.map(p => (
                <Tag
                  key={p}
                  color={p.startsWith('good:') ? 'green' : p.startsWith('bad:') ? 'red' : 'blue'}
                  style={{ marginBottom: 4 }}
                >
                  {p}
                </Tag>
              ))}
            </div>
          )}
        </Card>
      )}

      {/* LLM 评语 */}
      {hasLlm && d.llm_reasoning && (
        <Card type="inner" title="LLM 评语" size="small" style={{ marginTop: 12 }}>
          <Typography.Text style={{ color: '#d9d9d9' }}>{d.llm_reasoning}</Typography.Text>
          {d.llm_suggested_task_type && (
            <div style={{ marginTop: 8 }}>
              <Tag color="geekblue">建议类型: {d.llm_suggested_task_type}</Tag>
            </div>
          )}
          {d.llm_eval_model && (
            <Typography.Text type="secondary" style={{ fontSize: 11, display: 'block', marginTop: 4 }}>
              评估模型: {d.llm_eval_model}
            </Typography.Text>
          )}
        </Card>
      )}

      {/* Flag 标记 */}
      {d.rule_flags.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <Typography.Text type="secondary" style={{ marginRight: 8 }}>标记:</Typography.Text>
          {d.rule_flags.map(f => <Tag key={f} color="red">{f}</Tag>)}
        </div>
      )}

      {/* 评分元信息 */}
      {d.scored_at && (
        <Typography.Text type="secondary" style={{ fontSize: 11, display: 'block', marginTop: 8 }}>
          评分时间: {d.scored_at} · 版本: v{d.score_version}
        </Typography.Text>
      )}
    </Card>
  )
}
