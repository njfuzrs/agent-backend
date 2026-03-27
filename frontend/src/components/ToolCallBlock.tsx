import { useState } from 'react'
import { Tag, Typography } from 'antd'
import {
  ToolOutlined, CheckCircleOutlined, CloseCircleOutlined,
  DownOutlined, RightOutlined,
} from '@ant-design/icons'
import type { TrajectoryStep } from '../types/trajectory'

interface Props {
  action: TrajectoryStep
  observation?: TrajectoryStep
  forceExpanded?: boolean
}

const TOOL_COLORS: Record<string, string> = {
  Read: '#1668dc',
  Edit: '#d89614',
  Write: '#d89614',
  Bash: '#d32029',
  Grep: '#13a8a8',
  Glob: '#13a8a8',
  Agent: '#722ed1',
  WebSearch: '#eb2f96',
  WebFetch: '#eb2f96',
}

function getToolSummary(action: TrajectoryStep): string {
  const input = action.tool_input || {}
  if (action.tool_name === 'Read' && input.file_path) return String(input.file_path)
  if (action.tool_name === 'Edit' && input.file_path) return String(input.file_path)
  if (action.tool_name === 'Write' && input.file_path) return String(input.file_path)
  if (action.tool_name === 'Bash' && input.command) return String(input.command).slice(0, 80)
  if (action.tool_name === 'Grep' && input.pattern) return `/${input.pattern}/`
  if (action.tool_name === 'Glob' && input.pattern) return String(input.pattern)
  if (action.tool_name === 'Agent' && input.prompt) return String(input.prompt).slice(0, 60)
  return ''
}

export default function ToolCallBlock({ action, observation, forceExpanded = false }: Props) {
  const [expanded, setExpanded] = useState(false)
  const isExpanded = forceExpanded || expanded
  const toolColor = TOOL_COLORS[action.tool_name || ''] || '#666'
  const summary = getToolSummary(action)
  const isError = observation?.is_error

  return (
    <div style={{
      marginBottom: 6,
      border: `1px solid ${toolColor}33`,
      borderRadius: 4,
      overflow: 'hidden',
    }}>
      {/* 标题栏 */}
      <div
        onClick={() => {
          if (!forceExpanded) {
            setExpanded(!expanded)
          }
        }}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '6px 12px',
          background: `${toolColor}11`,
          cursor: forceExpanded ? 'default' : 'pointer',
          userSelect: 'none',
        }}
      >
        {isExpanded ? <DownOutlined style={{ fontSize: 10 }} /> : <RightOutlined style={{ fontSize: 10 }} />}
        <ToolOutlined style={{ color: toolColor }} />
        <Tag color={toolColor} style={{ margin: 0 }}>{action.tool_name}</Tag>
        <Typography.Text style={{ color: '#999', fontSize: 12, flex: 1 }} ellipsis>
          {summary}
        </Typography.Text>
        {observation && (
          isError
            ? <CloseCircleOutlined style={{ color: '#ff4d4f' }} />
            : <CheckCircleOutlined style={{ color: '#52c41a' }} />
        )}
      </div>

      {/* 展开内容 */}
      {isExpanded && (
        <div style={{ padding: '8px 12px', background: '#1a1a1a' }}>
          {/* 工具输入 */}
          {action.tool_input && (
            <div style={{ marginBottom: 8 }}>
              <Typography.Text style={{ color: '#888', fontSize: 11 }}>输入参数</Typography.Text>
              <pre style={{
                margin: '4px 0 0',
                padding: 8,
                background: '#111',
                borderRadius: 4,
                color: '#d4d4d4',
                fontSize: 12,
                overflow: 'auto',
                maxHeight: 300,
                whiteSpace: 'pre-wrap',
                overflowWrap: 'anywhere',
                wordBreak: 'break-word',
              }}>
                {JSON.stringify(action.tool_input, null, 2)}
              </pre>
            </div>
          )}

          {/* 工具输出 */}
          {observation && (
            <div>
              <Typography.Text style={{ color: '#888', fontSize: 11 }}>
                输出结果 {isError && <Tag color="error" style={{ fontSize: 10 }}>错误</Tag>}
              </Typography.Text>
              <pre style={{
                margin: '4px 0 0',
                padding: 8,
                background: isError ? '#2a1215' : '#111',
                borderRadius: 4,
                color: isError ? '#ff7875' : '#d4d4d4',
                fontSize: 12,
                overflow: 'auto',
                maxHeight: 400,
                whiteSpace: 'pre-wrap',
                overflowWrap: 'anywhere',
                wordBreak: 'break-word',
              }}>
                {observation.content || '-'}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
