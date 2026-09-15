import { Card, Empty, Space, Tag, Typography } from 'antd'
import type { ContentBlock, HistoryEntry } from '../types/trajectory'
import DetailTextBlock from './DetailTextBlock'
import ThinkingBlock from './ThinkingBlock'

interface HistoryChatViewProps {
  history: HistoryEntry[]
}

export default function HistoryChatView({ history }: HistoryChatViewProps) {
  if (!history.length) {
    return <Empty description="暂无 History 数据" />
  }

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      {history.map((entry, index) => (
        <Card
          key={`${entry.role}-${index}`}
          size="small"
          title={
            <Space wrap>
              <Typography.Text style={{ color: '#fff' }}>{roleLabel(entry.role)}</Typography.Text>
              <Tag>{index + 1}</Tag>
              {entry.message_type ? <Tag color="blue">{entry.message_type}</Tag> : null}
              {entry.stop_reason ? <Tag color="purple">{entry.stop_reason}</Tag> : null}
            </Space>
          }
        >
          {entry.thought ? <ThinkingBlock content={entry.thought} /> : null}
          {renderContent(entry.content, entry.role)}
        </Card>
      ))}
    </div>
  )
}

function renderContent(content: string | ContentBlock[], role: HistoryEntry['role']) {
  if (typeof content === 'string') {
    return (
      <DetailTextBlock
        title={roleLabel(role)}
        color={roleColor(role).background}
        accent={roleColor(role).accent}
        content={content}
      />
    )
  }

  const blocks = content.filter((item): item is ContentBlock => !!item && typeof item === 'object')
  if (!blocks.length) {
    return <Typography.Text style={{ color: '#8c8c8c' }}>空内容</Typography.Text>
  }

  return (
    <div style={{ display: 'grid', gap: 10 }}>
      {blocks.map((block, index) => {
        if (block.type === 'text') {
          return (
            <DetailTextBlock
              key={`${block.type}-${index}`}
              title={roleLabel(role)}
              color={roleColor(role).background}
              accent={roleColor(role).accent}
              content={block.text || ''}
            />
          )
        }

        if (block.type === 'thinking') {
          return <ThinkingBlock key={`${block.type}-${index}`} content={block.text || ''} />
        }

        if (block.type === 'tool_use') {
          return (
            <DetailTextBlock
              key={`${block.type}-${index}`}
              title={`工具调用 · ${block.name || 'unknown'}`}
              color="#1b1b1b"
              accent="#13a8a8"
              content={JSON.stringify(block.input || block.arguments || {}, null, 2)}
            />
          )
        }

        if (block.type === 'tool_result') {
          return (
            <DetailTextBlock
              key={`${block.type}-${index}`}
              title={`工具结果 · ${block.tool_use_id || 'unknown'}`}
              color={block.is_error ? '#2a1215' : '#1b1b1b'}
              accent={block.is_error ? '#ff4d4f' : '#52c41a'}
              content={typeof block.content === 'string' ? block.content : JSON.stringify(block.content, null, 2)}
            />
          )
        }

        return (
          <DetailTextBlock
            key={`${block.type}-${index}`}
            title={`Block · ${block.type}`}
            color="#1b1b1b"
            accent="#8c8c8c"
            content={JSON.stringify(block, null, 2)}
          />
        )
      })}
    </div>
  )
}

function roleLabel(role: HistoryEntry['role']) {
  if (role === 'system') return 'System'
  if (role === 'assistant') return 'Assistant'
  return 'User'
}

function roleColor(role: HistoryEntry['role']) {
  if (role === 'system') {
    return { background: '#161b22', accent: '#8b949e' }
  }
  if (role === 'assistant') {
    return { background: '#14281d', accent: '#52c41a' }
  }
  return { background: '#14233b', accent: '#1677ff' }
}
