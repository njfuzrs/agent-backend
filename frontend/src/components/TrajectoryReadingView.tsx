import { Button, Card, Empty, Space, Switch, Tag, Typography } from 'antd'
import type { DetailModel } from '../utils/trajectoryDetail'
import DetailTextBlock from './DetailTextBlock'
import ThinkingBlock from './ThinkingBlock'
import ToolCallBlock from './ToolCallBlock'

interface TrajectoryReadingViewProps {
  model: DetailModel
  showSystem: boolean
  onShowSystemChange: (value: boolean) => void
  showThinking: boolean
  onShowThinkingChange: (value: boolean) => void
  expandTools: boolean
  onExpandToolsChange: (value: boolean) => void
}

export default function TrajectoryReadingView({
  model,
  showSystem,
  onShowSystemChange,
  showThinking,
  onShowThinkingChange,
  expandTools,
  onExpandToolsChange,
}: TrajectoryReadingViewProps) {
  if (!model.sections.length && !model.systemPrompts.length) {
    return <Empty description="暂无可阅读的对话流程" />
  }

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <Card size="small">
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
          <Space wrap size={[12, 12]}>
            <Tag>节点 {model.nodeCount}</Tag>
            <Tag color="purple">Thinking {model.thinkingCount}</Tag>
            <Tag color="blue">工具 {model.toolCount}</Tag>
          </Space>
          <Space wrap size={[16, 8]}>
            <Space size={6}>
              <Typography.Text style={{ color: '#8c8c8c' }}>系统提示</Typography.Text>
              <Switch size="small" checked={showSystem} onChange={onShowSystemChange} />
            </Space>
            <Space size={6}>
              <Typography.Text style={{ color: '#8c8c8c' }}>Thinking</Typography.Text>
              <Switch size="small" checked={showThinking} onChange={onShowThinkingChange} />
            </Space>
            <Space size={6}>
              <Typography.Text style={{ color: '#8c8c8c' }}>展开工具结果</Typography.Text>
              <Switch size="small" checked={expandTools} onChange={onExpandToolsChange} />
            </Space>
          </Space>
        </div>
      </Card>

      <div style={{ display: 'grid', gridTemplateColumns: '220px minmax(0, 1fr)', gap: 16, alignItems: 'start' }}>
        <Card
          size="small"
          title="流程大纲"
          styles={{ body: { padding: 12 } }}
          style={{ position: 'sticky', top: 16 }}
        >
          <div style={{ display: 'grid', gap: 8 }}>
            {showSystem && model.systemPrompts.length ? (
              <Button type="text" style={{ justifyContent: 'flex-start' }} onClick={() => scrollToNode('system-prompts')}>
                系统提示
              </Button>
            ) : null}
            {model.sections.map((section, index) => (
              <Button
                key={section.id}
                type="text"
                style={{ justifyContent: 'flex-start', height: 'auto', textAlign: 'left', paddingBlock: 6 }}
                onClick={() => scrollToNode(section.id)}
              >
                <div style={{ display: 'grid', gap: 2 }}>
                  <Typography.Text style={{ color: '#d9d9d9' }}>#{index + 1} {section.title}</Typography.Text>
                  <Typography.Text style={{ color: '#8c8c8c', fontSize: 12 }}>
                    {section.summary || '无摘要'}
                  </Typography.Text>
                </div>
              </Button>
            ))}
          </div>
        </Card>

        <div style={{ minWidth: 0 }}>
          {showSystem && model.systemPrompts.length ? (
            <section id="system-prompts" style={{ marginBottom: 16 }}>
              <Card title="系统提示" size="small">
                {model.systemPrompts.map(prompt => (
                  <DetailTextBlock
                    key={prompt.id}
                    title="System"
                    color="#161b22"
                    accent="#8b949e"
                    content={prompt.text}
                    badge="默认折叠区域"
                  />
                ))}
              </Card>
            </section>
          ) : null}

          <div style={{ display: 'grid', gap: 16 }}>
            {model.sections.map((section, index) => (
              <section key={section.id} id={section.id}>
                <Card
                  size="small"
                  title={
                    <Space wrap>
                      <Typography.Text style={{ color: '#fff' }}>{section.title}</Typography.Text>
                      <Tag color="blue">{`回合 ${index + 1}`}</Tag>
                    </Space>
                  }
                >
                  {section.nodes.map(node => {
                    if (node.kind === 'thinking' && !showThinking) {
                      return null
                    }

                    if (node.kind === 'tool') {
                      return (
                        <ToolCallBlock
                          key={node.id}
                          action={node.action}
                          observation={node.observation}
                          forceExpanded={expandTools}
                        />
                      )
                    }

                    if (node.kind === 'thinking') {
                      return <ThinkingBlock key={node.id} content={node.text} forceExpanded={expandTools} />
                    }

                    if (node.kind === 'assistant') {
                      return (
                        <DetailTextBlock
                          key={node.id}
                          title="Assistant"
                          color="#14281d"
                          accent="#52c41a"
                          content={node.text}
                        />
                      )
                    }

                    if (node.kind === 'user') {
                      return (
                        <DetailTextBlock
                          key={node.id}
                          title="User"
                          color="#14233b"
                          accent="#1677ff"
                          content={node.text}
                        />
                      )
                    }

                    if (node.kind === 'note') {
                      return (
                        <DetailTextBlock
                          key={node.id}
                          title="提示"
                          color="#2a2111"
                          accent="#faad14"
                          content={node.text}
                        />
                      )
                    }

                    return (
                      <DetailTextBlock
                        key={node.id}
                        title="异常"
                        color="#2a1215"
                        accent="#ff4d4f"
                        content={node.text}
                      />
                    )
                  })}
                </Card>
              </section>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function scrollToNode(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
