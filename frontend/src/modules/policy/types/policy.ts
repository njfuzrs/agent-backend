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

export type PolicySettings = {
  permissions?: PolicyPermissions
  policyLimits?: Record<string, PolicyLimitValue>
  allowManagedPermissionRulesOnly?: boolean
  disableAllHooks?: boolean
  allowManagedHooksOnly?: boolean
  disabledModes?: string[]
  disableBypassPermissionsMode?: 'disable' | 'allow'
  strictPluginOnlyCustomization?: boolean | string[]
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
