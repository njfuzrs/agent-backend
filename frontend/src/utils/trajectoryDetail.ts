import type { ContentBlock, HistoryEntry, TrajectoryStep } from '../types/trajectory'

export interface DetailTextNode {
  id: string
  kind: 'system' | 'user' | 'assistant' | 'note' | 'warning'
  text: string
  timestamp?: string
  isFinalAnswer?: boolean
}

export interface DetailThinkingNode {
  id: string
  kind: 'thinking'
  text: string
  timestamp?: string
}

export interface DetailToolNode {
  id: string
  kind: 'tool'
  action: TrajectoryStep
  observation?: TrajectoryStep
  timestamp?: string
}

export type DetailNode = DetailTextNode | DetailThinkingNode | DetailToolNode

export interface DetailSection {
  id: string
  title: string
  summary: string
  nodes: DetailNode[]
  hasFinalAnswer: boolean
}

export interface DetailModel {
  systemPrompts: DetailTextNode[]
  sections: DetailSection[]
  nodeCount: number
  thinkingCount: number
  toolCount: number
  finalAnswerNodeId?: string
  finalAnswerSectionId?: string
}

const SYSTEM_REMINDER_PREFIX = '<system-reminder>'

export function buildDetailModel(
  history: HistoryEntry[],
  steps: TrajectoryStep[],
  firstPrompt = '',
): DetailModel {
  const actionByUseId = new Map<string, TrajectoryStep>()
  const observationByUseId = new Map<string, TrajectoryStep>()

  for (const step of steps) {
    const toolUseId = String(step.tool_use_id || '')
    if (!toolUseId) continue
    if (step.message_type === 'action') {
      actionByUseId.set(toolUseId, step)
      continue
    }
    observationByUseId.set(toolUseId, step)
  }

  const systemPrompts: DetailTextNode[] = []
  const nodes: DetailNode[] = []
  const seenToolResultIds = new Set<string>()
  const emittedToolNodeIds = new Set<string>()

  history.forEach((entry, entryIndex) => {
    if (entry.role === 'system') {
      const text = stringifyContent(entry.content)
      if (text) {
        systemPrompts.push({
          id: `system-${entryIndex}`,
          kind: 'system',
          text,
          timestamp: entry.timestamp,
        })
      }
      return
    }

    const blocks = normalizeBlocks(entry.content)
    const thought = normalizeText(entry.thought)
    let thinkingAdded = false

    for (const [blockIndex, block] of blocks.entries()) {
      if (entry.role === 'assistant') {
        if (!thinkingAdded && thought) {
          nodes.push({
            id: `thinking-${entryIndex}`,
            kind: 'thinking',
            text: thought,
            timestamp: entry.timestamp,
          })
          thinkingAdded = true
        }

        if (block.type === 'tool_use') {
          const toolUseId = String(block.id || '')
          const action = actionByUseId.get(toolUseId) || {
            message_type: 'action',
            role: 'assistant',
            tool_name: block.name,
            tool_use_id: toolUseId,
            tool_input: block.input || block.arguments || {},
            content: '',
            timestamp: entry.timestamp,
          }

          const observation = observationByUseId.get(toolUseId)
          if (observation?.tool_use_id) {
            seenToolResultIds.add(observation.tool_use_id)
          }

          nodes.push({
            id: `tool-${toolUseId || `${entryIndex}-${blockIndex}`}`,
            kind: 'tool',
            action,
            observation,
            timestamp: action.timestamp || observation?.timestamp || entry.timestamp,
          })
          if (toolUseId) {
            emittedToolNodeIds.add(toolUseId)
          }
          continue
        }

        if (block.type === 'thinking') {
          const text = normalizeText(block.text)
          if (text) {
            nodes.push({
              id: `thinking-${entryIndex}-${blockIndex}`,
              kind: 'thinking',
              text,
              timestamp: entry.timestamp,
            })
          }
          continue
        }

        const text = stringifyContent(block)
        if (text) {
          nodes.push({
            id: `assistant-${entryIndex}-${blockIndex}`,
            kind: 'assistant',
            text,
            timestamp: entry.timestamp,
          })
        }
        continue
      }

      if (block.type === 'tool_result') {
        const toolUseId = String(block.tool_use_id || '')
        if (toolUseId) {
          seenToolResultIds.add(toolUseId)
        }

        const action = actionByUseId.get(toolUseId) || {
          message_type: 'action',
          role: 'assistant',
          tool_name: block.name,
          tool_use_id: toolUseId,
          tool_input: {},
          content: '',
          timestamp: entry.timestamp,
        }

        const observation = observationByUseId.get(toolUseId) || {
          message_type: 'observation',
          role: 'user',
          tool_use_id: toolUseId,
          content: stringifyContent(block.content),
          is_error: !!block.is_error,
          timestamp: entry.timestamp,
        }

        if (!toolUseId || !emittedToolNodeIds.has(toolUseId)) {
          nodes.push({
            id: `tool-result-${toolUseId || `${entryIndex}-${blockIndex}`}`,
            kind: 'tool',
            action,
            observation,
            timestamp: observation.timestamp || entry.timestamp,
          })
        }
        continue
      }

      const text = stringifyContent(block)
      if (!text) continue

      nodes.push({
        id: `${isSystemReminder(text) ? 'note' : 'user'}-${entryIndex}-${blockIndex}`,
        kind: isSystemReminder(text) ? 'note' : 'user',
        text,
        timestamp: entry.timestamp,
      })
    }

    if (!blocks.length && entry.role === 'assistant') {
      if (thought && !thinkingAdded) {
        nodes.push({
          id: `thinking-${entryIndex}`,
          kind: 'thinking',
          text: thought,
          timestamp: entry.timestamp,
        })
      }
      const text = stringifyContent(entry.content)
      if (text) {
        nodes.push({
          id: `assistant-${entryIndex}`,
          kind: 'assistant',
          text,
          timestamp: entry.timestamp,
        })
      }
    }
  })

  steps.forEach((step, stepIndex) => {
    if (step.message_type !== 'observation' || !step._orphan || !step.tool_use_id) {
      return
    }
    if (seenToolResultIds.has(step.tool_use_id)) {
      return
    }

    const action = actionByUseId.get(step.tool_use_id)
    nodes.push({
      id: `orphan-${step.tool_use_id}-${stepIndex}`,
      kind: 'warning',
      text: `存在未关联的工具结果${action?.tool_name ? `：${action.tool_name}` : ''}\n\n${normalizeText(step.content) || '[空结果]'}`,
      timestamp: step.timestamp,
    })
  })

  let finalAnswerNodeId: string | undefined
  for (let index = nodes.length - 1; index >= 0; index -= 1) {
    const node = nodes[index]
    if (node.kind === 'assistant') {
      node.isFinalAnswer = true
      finalAnswerNodeId = node.id
      break
    }
  }

  const sections = buildSections(nodes, firstPrompt)
  const finalAnswerSectionId = sections.find(section => section.hasFinalAnswer)?.id
  return {
    systemPrompts,
    sections,
    nodeCount: nodes.length,
    thinkingCount: nodes.filter(node => node.kind === 'thinking').length,
    toolCount: nodes.filter(node => node.kind === 'tool').length,
    finalAnswerNodeId,
    finalAnswerSectionId,
  }
}

function buildSections(nodes: DetailNode[], firstPrompt: string): DetailSection[] {
  const sections: DetailSection[] = []
  let current: DetailSection | null = null

  nodes.forEach((node, index) => {
    if (node.kind === 'user') {
      current = {
        id: `section-${index}`,
        title: buildSectionTitle(node.text, sections.length + 1),
        summary: buildSectionSummary(node.text),
        nodes: [node],
        hasFinalAnswer: !!node.isFinalAnswer,
      }
      sections.push(current)
      return
    }

    if (!current) {
      current = {
        id: `section-${index}`,
        title: firstPrompt ? 'Prompt 1' : '会话过程',
        summary: firstPrompt ? buildSectionSummary(firstPrompt) : '系统提示后的执行流程',
        nodes: [],
        hasFinalAnswer: false,
      }
      sections.push(current)
    }

    current.nodes.push(node)
    if (node.kind === 'assistant' && node.isFinalAnswer) {
      current.hasFinalAnswer = true
    }
  })

  return sections
}

function buildSectionTitle(text: string, index: number): string {
  const stripped = buildSectionSummary(text)
  return stripped ? `Prompt ${index}` : `回合 ${index}`
}

function buildSectionSummary(text: string): string {
  return normalizeText(text)
    .replace(/\s+/g, ' ')
    .slice(0, 48)
}

function normalizeBlocks(content: HistoryEntry['content']): ContentBlock[] {
  if (Array.isArray(content)) {
    return content.filter((item): item is ContentBlock => !!item && typeof item === 'object')
  }

  const text = normalizeText(content)
  return text ? [{ type: 'text', text }] : []
}

export function stringifyContent(value: unknown): string {
  if (typeof value === 'string') {
    return normalizeText(value)
  }

  if (Array.isArray(value)) {
    const parts = value
      .map(item => {
        if (typeof item === 'string') {
          return normalizeText(item)
        }
        if (!item || typeof item !== 'object') {
          return ''
        }

        const block = item as ContentBlock
        if (block.type === 'text' || block.type === 'thinking') {
          return normalizeText(block.text)
        }
        if (block.type === 'tool_result') {
          return stringifyContent(block.content)
        }
        return ''
      })
      .filter(Boolean)

    return parts.join('\n\n').trim()
  }

  if (value && typeof value === 'object') {
    const maybeBlock = value as ContentBlock
    if (maybeBlock.text) {
      return normalizeText(maybeBlock.text)
    }
    if (maybeBlock.content) {
      return stringifyContent(maybeBlock.content)
    }
  }

  return ''
}

function normalizeText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function isSystemReminder(text: string): boolean {
  return text.startsWith(SYSTEM_REMINDER_PREFIX)
}
