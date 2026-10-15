/** 管理台策略。settings 是客户端 PolicySettings 子集，不含 source。 */

export type PolicyScopeType = 'device' | 'team' | 'org'

export type PolicyPermissions = {
  allow?: string[]
  deny?: string[]
  ask?: string[]
}

export type PolicyLimitValue = {
  allowed: boolean
  reason?: string
}

/** 插件来源白名单的一项。形状由 sid-code 客户端定。 */
export type KnownMarketplace = {
  source: 'url'
  url: string
}

export type PolicySettings = {
  permissions?: PolicyPermissions
  policyLimits?: Record<string, PolicyLimitValue>
  allowManagedPermissionRulesOnly?: boolean
  disableAllHooks?: boolean
  allowManagedHooksOnly?: boolean
  disabledModes?: string[]
  disableBypassPermissionsMode?: 'disable' | 'allow'
  strictPluginOnlyCustomization?: boolean | string[]
  bridgeEnabled?: boolean
  /** 省略 = 不限制；数组 = 只允许这些市场 index URL；空数组 = 除内置外禁一切插件 */
  strictKnownMarketplaces?: KnownMarketplace[]
}

export type PolicyItem = {
  id: number
  scope_type: PolicyScopeType
  scope_id: string
  org_id: string
  settings: PolicySettings
  disabled: boolean
  created_at: string
  updated_at: string
  updated_by: string
  etag: string
  /** 不阻止写入的提醒（如白名单里漏了本企业市场） */
  warnings: string[]
}

export type PolicyAuditItem = {
  id: number
  policy_id: number | null
  scope_type: string
  scope_id: string
  action: 'create' | 'update' | 'delete' | 'disable' | 'enable' | string
  old_settings: PolicySettings | null
  new_settings: PolicySettings | null
  reason: string
  actor: string
  created_at: string
}

/** 一层生效策略的预览形状 */
export type PolicyLayer = {
  layer: PolicyScopeType
  policy_id: number
  scope_id: string
  org_id: string
  settings: PolicySettings
}

/** 预览命中。shadowed = 被命中层整份遮蔽的上层（不合并字段，它们对该设备完全不生效） */
export type PolicyEvaluate = PolicyLayer & {
  shadowed: PolicyLayer[]
}
