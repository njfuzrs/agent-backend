import { useState } from 'react'
import { Button, Card, Empty, Space, Switch, Tag, Tooltip, Typography } from 'antd'
import { DownOutlined, RightOutlined } from '@ant-design/icons'
import type { DetailModel, DetailNode, DetailSection } from '../../../utils/trajectoryDetail'
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

const PANEL_HEIGHT = 'max(360px, min(72vh, calc(100vh - 320px)))'

export default function TrajectoryReadingView({
  model,
  showSystem,
  onShowSystemChange,
  showThinking,
  onShowThinkingChange,
  expandTools,
  onExpandToolsChange,
}: TrajectoryReadingViewProps) {
  const [collapsedSectionIds, setCollapsedSectionIds] = useState<string[]>([])

  if (!model.sections.length && !model.systemPrompts.length) {
    return <Empty description="暂无可阅读的对话流程" />
  }

  const isCollapsed = (sectionId: string) => collapsedSectionIds.includes(sectionId)

  const toggleSection = (sectionId: string) => {
    setCollapsedSectionIds(current =>
      current.includes(sectionId)
        ? current.filter(item => item !== sectionId)
        : [...current, sectionId],
    )
  }

  const collapseAll = () => {
    setCollapsedSectionIds(model.sections.map(section => section.id))
  }

  const expandAll = () => {
    setCollapsedSectionIds([])
  }

  const revealAndScroll = (sectionId: string, targetId?: string) => {
    if (!sectionId && !targetId) {
      return
    }

    if (sectionId && isCollapsed(sectionId)) {
      setCollapsedSectionIds(current => current.filter(item => item !== sectionId))
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => {
          scrollToNode(targetId || sectionId)
        })
      })
      return
    }

    scrollToNode(targetId || sectionId)
  }

  const canJumpToFinalAnswer = !!model.finalAnswerSectionId

  return (
    <div style={{ display: 'grid', gap: 16, minWidth: 0 }}>
      <Card size="small">
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
          <Space wrap size={[12, 12]}>
            <Tag>节点 {model.nodeCount}</Tag>
            <Tag color="purple">Thinking {model.thinkingCount}</Tag>
            <Tag color="blue">工具 {model.toolCount}</Tag>
            {canJumpToFinalAnswer ? (
              <Button
                size="small"
                onClick={() =>
                  revealAndScroll(
                    model.finalAnswerSectionId || '',
                    model.finalAnswerNodeId || model.finalAnswerSectionId,
                  )
                }
              >
                跳到最终回答
              </Button>
            ) : null}
          </Space>

          <Space wrap size={[16, 8]}>
            <Button size="small" onClick={expandAll}>
              展开全部回合
            </Button>
            <Button size="small" onClick={collapseAll}>
              折叠全部回合
            </Button>
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

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'flex-start', minWidth: 0 }}>
        <div style={{ flex: '0 1 260px', width: 260, maxWidth: '100%', minWidth: 0 }}>
          <Card
            size="small"
            title="流程大纲"
            styles={{
              body: {
                padding: 12,
                height: PANEL_HEIGHT,
                overflowY: 'auto',
                overflowX: 'hidden',
              },
            }}
          >
            <div style={{ display: 'grid', gap: 8 }}>
              {showSystem && model.systemPrompts.length ? (
                <Button
                  type="text"
                  style={{ justifyContent: 'flex-start', width: '100%', minWidth: 0 }}
                  onClick={() => scrollToNode('system-prompts')}
                >
                  <Typography.Text
                    style={{
                      color: '#d9d9d9',
                      display: 'block',
                      width: '100%',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      textAlign: 'left',
                    }}
                  >
                    系统提示
                  </Typography.Text>
                </Button>
              ) : null}

              {model.sections.map((section, index) => (
                <Button
                  key={section.id}
                  type="text"
                  style={{
                    justifyContent: 'flex-start',
                    width: '100%',
                    minWidth: 0,
                    height: 'auto',
                    paddingBlock: 6,
                    textAlign: 'left',
                  }}
                  onClick={() => revealAndScroll(section.id)}
                >
                  <div style={{ display: 'grid', gap: 2, minWidth: 0, width: '100%' }}>
                    <Typography.Text
                      style={{
                        color: section.hasFinalAnswer ? '#95de64' : '#d9d9d9',
                        display: 'block',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      #{index + 1} {section.title}
                    </Typography.Text>
                    <Tooltip title={section.summary || '无摘要'} placement="right">
                      <Typography.Text
                        style={{
                          color: '#8c8c8c',
                          fontSize: 12,
                          display: 'block',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {section.summary || '无摘要'}
                      </Typography.Text>
                    </Tooltip>
                    <Tooltip title={buildSectionStats(section, showThinking)} placement="right">
                      <Typography.Text
                        style={{
                          color: '#8c8c8c',
                          fontSize: 12,
                          display: 'block',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {buildSectionStats(section, showThinking)}
                      </Typography.Text>
                    </Tooltip>
                  </div>
                </Button>
              ))}
            </div>
          </Card>
        </div>

        <div style={{ flex: '1 1 720px', minWidth: 0 }}>
          <div
            style={{
              height: PANEL_HEIGHT,
              overflowY: 'auto',
              overflowX: 'hidden',
              paddingRight: 4,
            }}
          >
            {showSystem && model.systemPrompts.length ? (
              <section id="system-prompts" style={{ marginBottom: 16 }}>
                <Card title="系统提示" size="small">
                  {model.systemPrompts.map(prompt => (
                    <DetailTextBlock
                      key={prompt.id}
                      id={prompt.id}
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

            <div style={{ display: 'grid', gap: 16, minWidth: 0 }}>
              {model.sections.map((section, index) => (
                <section key={section.id} id={section.id} style={{ minWidth: 0 }}>
                  <Card
                    size="small"
                    title={
                      <Space wrap>
                        <Typography.Text style={{ color: '#fff' }}>{section.title}</Typography.Text>
                        <Tag color="blue">{`回合 ${index + 1}`}</Tag>
                        {section.hasFinalAnswer ? <Tag color="success">最终回答</Tag> : null}
                      </Space>
                    }
                    extra={
                      <Button type="text" onClick={() => toggleSection(section.id)}>
                        {isCollapsed(section.id) ? <RightOutlined /> : <DownOutlined />}
                        {isCollapsed(section.id) ? '展开' : '折叠'}
                      </Button>
                    }
                    styles={{ body: { overflowX: 'hidden' } }}
                  >
                    {isCollapsed(section.id) ? (
                      <Tooltip title={section.summary || '该回合已折叠'}>
                        <Typography.Text
                          style={{
                            color: '#8c8c8c',
                            display: 'block',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {section.summary || '该回合已折叠'}
                        </Typography.Text>
                      </Tooltip>
                    ) : (
                      section.nodes.map(node => renderNode(node, showThinking, expandTools))
                    )}
                  </Card>
                </section>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function renderNode(node: DetailNode, showThinking: boolean, expandTools: boolean) {
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
        id={node.id}
        title={node.isFinalAnswer ? 'Assistant · 最终回答' : 'Assistant'}
        color="#14281d"
        accent={node.isFinalAnswer ? '#95de64' : '#52c41a'}
        content={node.text}
        badge={node.isFinalAnswer ? '最终回答' : undefined}
        highlighted={node.isFinalAnswer}
      />
    )
  }

  if (node.kind === 'user') {
    return (
      <DetailTextBlock
        key={node.id}
        id={node.id}
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
        id={node.id}
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
      id={node.id}
      title="异常"
      color="#2a1215"
      accent="#ff4d4f"
      content={node.text}
    />
  )
}

function buildSectionStats(section: DetailSection, showThinking: boolean) {
  const toolCount = section.nodes.filter(node => node.kind === 'tool').length
  const assistantCount = section.nodes.filter(node => node.kind === 'assistant').length
  const thinkingCount = showThinking ? section.nodes.filter(node => node.kind === 'thinking').length : 0
  const parts = [`工具 ${toolCount}`, `回复 ${assistantCount}`]

  if (thinkingCount) {
    parts.push(`Thinking ${thinkingCount}`)
  }

  return parts.join(' · ')
}

function scrollToNode(id: string) {
  if (!id) {
    return
  }

  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
