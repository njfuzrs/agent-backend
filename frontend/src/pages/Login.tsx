import { useEffect, useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { Alert, Button, Card, Divider, Form, Input, Typography, message } from 'antd'
import { useQueryClient } from '@tanstack/react-query'
import {
  checkAuth,
  feishuLoginUrl,
  fetchLoginOptions,
  login,
  type LoginOptions,
} from '../modules/trajectory/services/api'

interface LoginForm {
  username: string
  password: string
}

// 后端回调失败时带回的 ?error=，见 backend/app/modules/auth/router/login.py 的 LOGIN_ERRORS
const LOGIN_ERROR_TEXT: Record<string, string> = {
  access_denied: '你在飞书授权页取消了授权。',
  invalid_state: '登录链接已过期或不是从本页发起的，请重新点击「飞书登录」。',
  feishu_failed: '飞书授权失败，请稍后重试。',
  tenant_mismatch: '该飞书账号不属于本企业。',
  revoked: '你的账号已被管理员停用。',
}

function resolveRedirect(fromState: unknown, searchFrom: string | null): string {
  if (typeof fromState === 'string' && fromState.startsWith('/') && !fromState.startsWith('//')) {
    return fromState
  }
  if (searchFrom && searchFrom.startsWith('/') && !searchFrom.startsWith('//')) {
    return searchFrom
  }
  return '/'
}

export default function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const [submitting, setSubmitting] = useState(false)
  const [checking, setChecking] = useState(true)
  const [options, setOptions] = useState<LoginOptions | null>(null)
  const [form] = Form.useForm<LoginForm>()
  const queryClient = useQueryClient()

  const fromState = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname
  const redirectTo = resolveRedirect(fromState, searchParams.get('from'))
  const errorCode = searchParams.get('error')

  useEffect(() => {
    let cancelled = false
    Promise.all([checkAuth(), fetchLoginOptions()]).then(([ok, opts]) => {
      if (cancelled) return
      if (ok) {
        navigate(redirectTo, { replace: true })
        return
      }
      setOptions(opts)
      setChecking(false)
    })
    return () => {
      cancelled = true
    }
  }, [navigate, redirectTo])

  const handleLogin = async () => {
    const values = await form.validateFields()
    setSubmitting(true)
    try {
      await login(values.username, values.password)
      queryClient.clear()
      message.success('登录成功')
      navigate(redirectTo, { replace: true })
    } catch {
      message.error('用户名或密码错误')
    } finally {
      setSubmitting(false)
    }
  }

  if (checking || !options) {
    return <div style={{ minHeight: '100vh', background: '#141414' }} />
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#141414',
        padding: 16,
      }}
    >
      <Card style={{ width: 400, maxWidth: '100%' }}>
        <Typography.Title level={3} style={{ marginTop: 0, marginBottom: 8 }}>
          Agent Backend
        </Typography.Title>
        <Typography.Paragraph type="secondary">登录后浏览轨迹与设备。</Typography.Paragraph>
        {errorCode && (
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 16 }}
            message={LOGIN_ERROR_TEXT[errorCode] || '登录失败，请重试。'}
          />
        )}
        {options.feishu_enabled && (
          <Button type="primary" block size="large" href={feishuLoginUrl(redirectTo)}>
            飞书登录
          </Button>
        )}
        {options.feishu_enabled && options.password_enabled && (
          <Divider plain style={{ fontSize: 12 }}>应急：管理员口令</Divider>
        )}
        {options.password_enabled && (
          <Form form={form} layout="vertical" initialValues={{ username: 'admin' }} onFinish={handleLogin}>
            <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
              <Input autoFocus={!options.feishu_enabled} autoComplete="username" />
            </Form.Item>
            <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
              <Input.Password autoComplete="current-password" />
            </Form.Item>
            <Button type={options.feishu_enabled ? 'default' : 'primary'} htmlType="submit" loading={submitting} block>
              口令登录
            </Button>
          </Form>
        )}
        {!options.feishu_enabled && !options.password_enabled && (
          <Alert type="warning" showIcon message="服务端未开放任何登录方式，请联系管理员检查配置。" />
        )}
      </Card>
    </div>
  )
}
