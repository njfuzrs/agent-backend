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

export type PolicyEvaluate = {
  layer: PolicyScopeType
  policy_id: number
  scope_id: string
  org_id: string
  settings: PolicySettings
}
