export type UserItem = {
  id: number
  provider: string
  union_id: string
  name: string
  email: string
  role: 'admin' | 'member'
  status: 'active' | 'revoked'
  created_at: string
  last_login_at: string | null
}

export type AuthAuditItem = {
  id: number
  created_at: string
  user_id: number | null
  actor: string
  event: string
  detail_json: string | null
  request_id: string | null
}
