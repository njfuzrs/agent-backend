import { Tag, Typography } from 'antd'

interface DetailTextBlockProps {
  title: string
  color: string
  accent: string
  content: string
  badge?: string
}

export default function DetailTextBlock({
  title,
  color,
  accent,
  content,
  badge,
}: DetailTextBlockProps) {
  return (
    <div
      style={{
        marginBottom: 12,
        padding: '14px 16px',
        background: color,
        borderLeft: `4px solid ${accent}`,
        borderRadius: 10,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', marginBottom: 8 }}>
        <Typography.Text strong style={{ color: '#fff' }}>
          {title}
        </Typography.Text>
        {badge ? <Tag style={{ margin: 0 }}>{badge}</Tag> : null}
      </div>
      <div style={{ color: '#d9d9d9', whiteSpace: 'pre-wrap', lineHeight: 1.65, fontSize: 13 }}>
        {content}
      </div>
    </div>
  )
}
