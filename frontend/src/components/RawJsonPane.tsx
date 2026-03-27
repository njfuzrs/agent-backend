import { Empty } from 'antd'

interface RawJsonPaneProps {
  content: string
}

export default function RawJsonPane({ content }: RawJsonPaneProps) {
  if (!content) {
    return <Empty description="暂无原始 JSON 数据" />
  }

  return (
    <pre
      style={{
        margin: 0,
        padding: 16,
        background: '#111',
        borderRadius: 10,
        color: '#d4d4d4',
        overflow: 'auto',
        whiteSpace: 'pre-wrap',
        lineHeight: 1.6,
        fontSize: 12,
      }}
    >
      {content}
    </pre>
  )
}
