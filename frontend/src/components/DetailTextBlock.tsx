import { Tag, Typography } from 'antd'

interface DetailTextBlockProps {
  id?: string
  title: string
  color: string
  accent: string
  content: string
  badge?: string
  highlighted?: boolean
}

export default function DetailTextBlock({
  id,
  title,
  color,
  accent,
  content,
  badge,
  highlighted = false,
}: DetailTextBlockProps) {
  return (
    <div
      id={id}
      style={{
        marginBottom: 12,
        padding: '14px 16px',
        background: highlighted ? `linear-gradient(135deg, ${color}, #1f3d1f)` : color,
        borderLeft: `4px solid ${accent}`,
        borderRadius: 10,
        boxShadow: highlighted ? '0 0 0 1px #95de64 inset, 0 10px 24px rgba(149, 222, 100, 0.12)' : 'none',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', marginBottom: 8 }}>
        <Typography.Text strong style={{ color: '#fff' }}>
          {title}
        </Typography.Text>
        {badge ? <Tag style={{ margin: 0 }}>{badge}</Tag> : null}
      </div>
      <div style={{ color: '#d9d9d9', whiteSpace: 'pre-wrap', lineHeight: 1.65, fontSize: 13, overflowWrap: 'anywhere' }}>
        {content}
      </div>
    </div>
  )
}
