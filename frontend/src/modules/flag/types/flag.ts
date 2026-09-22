/** 客户端 `feature-flags.ts` 的 FlagValue：四种 JSON 形态之一。 */
export type FlagValue = boolean | number | string | Record<string, unknown> | unknown[]

export type FlagItem = {
  key: string
  value: FlagValue
  description: string
  /** true = 不进下发，客户端按「远程已删除」回落默认值 */
  disabled: boolean
  created_at: string
  updated_at: string
  updated_by: string
}

export type FlagListResponse = {
  items: FlagItem[]
}

export type FlagAuditItem = {
  id: number
  key: string
  action: 'create' | 'update' | 'delete' | 'disable' | 'enable'
  old_value: FlagValue | null
  new_value: FlagValue | null
  reason: string
  actor: string
  created_at: string
}

export type FlagAuditListResponse = {
  items: FlagAuditItem[]
}
