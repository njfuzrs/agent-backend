import { useState } from 'react'
import { Typography } from 'antd'
import { BulbOutlined, DownOutlined, RightOutlined } from '@ant-design/icons'

interface Props {
  content: string
  forceExpanded?: boolean
}

export default function ThinkingBlock({ content, forceExpanded = false }: Props) {
  const [expanded, setExpanded] = useState(false)
  const isExpanded = forceExpanded || expanded

  if (!content) return null

  return (
    <div style={{
      marginBottom: 6,
      border: '1px solid #722ed133',
      borderRadius: 4,
      overflow: 'hidden',
    }}>
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
          padding: '4px 12px',
          background: '#722ed111',
          cursor: forceExpanded ? 'default' : 'pointer',
          userSelect: 'none',
        }}
      >
        {isExpanded ? <DownOutlined style={{ fontSize: 10 }} /> : <RightOutlined style={{ fontSize: 10 }} />}
        <BulbOutlined style={{ color: '#722ed1' }} />
        <Typography.Text style={{ color: '#b37feb', fontSize: 12 }}>
          Thinking ({content.length.toLocaleString()} chars)
        </Typography.Text>
      </div>

      {isExpanded && (
        <div style={{
          padding: '8px 12px',
          background: '#1a1520',
          color: '#c9b3e0',
          fontSize: 13,
          whiteSpace: 'pre-wrap',
          maxHeight: 400,
          overflow: 'auto',
          lineHeight: 1.5,
          overflowWrap: 'anywhere',
          wordBreak: 'break-word',
        }}>
          {content}
        </div>
      )}
    </div>
  )
}
