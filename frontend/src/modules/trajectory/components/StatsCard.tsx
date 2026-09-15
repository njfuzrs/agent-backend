import { Card, Typography } from 'antd'

interface StatsCardProps {
  title: string
  value: string
  hint?: string
}

export default function StatsCard({ title, value, hint }: StatsCardProps) {
  return (
    <Card size="small" style={{ height: '100%' }}>
      <Typography.Text type="secondary">{title}</Typography.Text>
      <div style={{ fontSize: 28, fontWeight: 700, marginTop: 8 }}>{value}</div>
      {hint ? (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {hint}
        </Typography.Text>
      ) : null}
    </Card>
  )
}
