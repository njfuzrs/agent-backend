import { Tag, Typography } from 'antd'
import { UserOutlined, RobotOutlined } from '@ant-design/icons'
import type { TrajectoryStep } from '../types/trajectory'
import ToolCallBlock from './ToolCallBlock'
import ThinkingBlock from './ThinkingBlock'

interface Props {
  steps: TrajectoryStep[]
}

/** 将 trajectory 步骤按对话轮次分组 */
function groupSteps(steps: TrajectoryStep[]) {
  const groups: { type: 'user' | 'assistant' | 'action' | 'observation'; items: TrajectoryStep[] }[] = []

  for (const step of steps) {
    if (step.message_type === 'action') {
      groups.push({ type: 'action', items: [step] })
    } else if (step.message_type === 'observation') {
      // 尝试合并到上一个 action
      const last = groups[groups.length - 1]
      if (last && last.type === 'action') {
        last.items.push(step)
      } else {
        groups.push({ type: 'observation', items: [step] })
      }
    } else {
      groups.push({ type: step.role === 'user' ? 'user' : 'assistant', items: [step] })
    }
  }
  return groups
}

export default function Timeline({ steps }: Props) {
  if (!steps.length) {
    return <Typography.Text style={{ color: '#999' }}>无轨迹步骤数据</Typography.Text>
  }

  const groups = groupSteps(steps)

  return (
    <div style={{ maxWidth: 900 }}>
      {groups.map((group, i) => {
        const action = group.items[0]
        const observation = group.items[1]

        if (group.type === 'action' && action.tool_name) {
          return (
            <div key={i} style={{ marginBottom: 8 }}>
              {action.thought && <ThinkingBlock content={action.thought} />}
              <ToolCallBlock action={action} observation={observation} />
            </div>
          )
        }

        if (group.type === 'user') {
          return (
            <div key={i} style={{
              marginBottom: 12,
              padding: '10px 14px',
              background: '#1a2332',
              borderLeft: '3px solid #1668dc',
              borderRadius: 4,
            }}>
              <div style={{ marginBottom: 4 }}>
                <UserOutlined style={{ color: '#1668dc', marginRight: 6 }} />
                <Tag color="blue">User</Tag>
              </div>
              <div style={{ color: '#d4d4d4', whiteSpace: 'pre-wrap', fontSize: 13 }}>
                {action.content || action.thought || '-'}
              </div>
            </div>
          )
        }

        if (group.type === 'assistant') {
          return (
            <div key={i} style={{
              marginBottom: 12,
              padding: '10px 14px',
              background: '#1a2a1a',
              borderLeft: '3px solid #52c41a',
              borderRadius: 4,
            }}>
              <div style={{ marginBottom: 4 }}>
                <RobotOutlined style={{ color: '#52c41a', marginRight: 6 }} />
                <Tag color="green">Assistant</Tag>
              </div>
              {action.thought && <ThinkingBlock content={action.thought} />}
              <div style={{ color: '#d4d4d4', whiteSpace: 'pre-wrap', fontSize: 13 }}>
                {action.content || action.action || '-'}
              </div>
            </div>
          )
        }

        // observation without action
        return (
          <div key={i} style={{
            marginBottom: 8,
            padding: '8px 12px',
            background: '#1f1f1f',
            borderRadius: 4,
            fontSize: 13,
            color: '#999',
          }}>
            <pre style={{ margin: 0, whiteSpace: 'pre-wrap', maxHeight: 200, overflow: 'auto' }}>
              {action.content || JSON.stringify(action, null, 2)}
            </pre>
          </div>
        )
      })}
    </div>
  )
}
