import { Card, Collapse, Empty } from 'antd'
import type { HistoryEntry, TrajectoryStep } from '../types/trajectory'

interface TrajectoryDebugViewProps {
  steps: TrajectoryStep[]
  history: HistoryEntry[]
  info: Record<string, unknown> | undefined
}

export default function TrajectoryDebugView({ steps, history, info }: TrajectoryDebugViewProps) {
  if (!steps.length && !history.length && !info) {
    return <Empty description="暂无调试数据" />
  }

  return (
    <Collapse
      size="small"
      defaultActiveKey={['info']}
      items={[
        {
          key: 'info',
          label: 'info',
          children: <JsonCard value={info || {}} />,
        },
        {
          key: 'trajectory',
          label: `trajectory (${steps.length})`,
          children: <JsonCard value={steps} />,
        },
        {
          key: 'history',
          label: `history (${history.length})`,
          children: <JsonCard value={history} />,
        },
      ]}
    />
  )
}

function JsonCard({ value }: { value: unknown }) {
  return (
    <Card size="small">
      <pre
        style={{
          margin: 0,
          maxHeight: 720,
          overflow: 'auto',
          whiteSpace: 'pre-wrap',
          color: '#d4d4d4',
          lineHeight: 1.6,
          fontSize: 12,
        }}
      >
        {JSON.stringify(value, null, 2)}
      </pre>
    </Card>
  )
}
