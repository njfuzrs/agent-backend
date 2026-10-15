/**
 * 退出状态与结束方式的展示口径（列表筛选 / 列表图标 / 详情页共用，单一事实源）。
 *
 * 两个维度不要混：
 * - exit_status：结果。成功率只看它，成功集合在后端 stats_service.SUCCESS_EXIT_STATUSES。
 * - end_source：会话怎么结束的（sid-code metadata.end_source）。空闲关终端的会话
 *   exit_status 按末轮推断（多为 end_turn），end_source = terminal_closed。
 */

export type StatusTone = 'success' | 'warning' | 'error' | 'default'

export interface ExitStatusInfo {
  tone: StatusTone
  label: string
}

/** 已知 exit_status。未登记的值按原文 + default 色展示，不吞掉。 */
export const EXIT_STATUS_INFO: Record<string, ExitStatusInfo> = {
  end_turn: { tone: 'success', label: '正常完成' },
  tool_use: { tone: 'warning', label: '停在工具调用' },
  user_interrupt: { tone: 'warning', label: '用户中断' },
  max_turns: { tone: 'warning', label: '轮次用尽' },
  budget_exceeded: { tone: 'default', label: '预算停止' },
  abort: { tone: 'error', label: '强制终止' },
  error: { tone: 'error', label: '运行错误' },
  clear: { tone: 'default', label: '/clear 清空' },
  terminal_closed: { tone: 'default', label: '未提问即关闭' },
  incomplete: { tone: 'default', label: '未收尾' },
  unknown: { tone: 'default', label: 'unknown' },
}

export function exitStatusInfo(value: string | null | undefined): ExitStatusInfo {
  if (!value) return EXIT_STATUS_INFO.unknown
  return EXIT_STATUS_INFO[value] ?? { tone: 'default', label: value }
}

/** 列表页「退出状态」筛选项：值与后端 exit_status 精确匹配。 */
export const EXIT_STATUS_FILTER_OPTIONS = Object.entries(EXIT_STATUS_INFO).map(([value, info]) => ({
  value,
  label: `${info.label}（${value}）`,
}))

/** end_source 的中文名。未登记的值原样展示。 */
export const END_SOURCE_LABEL: Record<string, string> = {
  exit: '正常退出',
  abort: '中断退出',
  terminal_closed: '关闭终端',
  error: '异常退出',
  clear: '/clear',
  other: '其他',
}

export function endSourceLabel(value: string | null | undefined): string | null {
  if (!value) return null
  return END_SOURCE_LABEL[value] ?? value
}
