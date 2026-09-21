import { useEffect, useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { Button, Card, Form, Input, Typography, message } from 'antd'
import { useQueryClient } from '@tanstack/react-query'
import { checkAuth, login } from '../modules/trajectory/services/api'

interface LoginForm {
  username: string
  password: string
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
  const [form] = Form.useForm<LoginForm>()
  const queryClient = useQueryClient()

  const fromState = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname
  const redirectTo = resolveRedirect(fromState, searchParams.get('from'))

  useEffect(() => {
    let cancelled = false
    checkAuth().then(ok => {
      if (cancelled) return
      if (ok) {
        navigate(redirectTo, { replace: true })
        return
      }
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

  if (checking) {
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
        padding: 24,
      }}
    >
      <Card style={{ width: 400, maxWidth: '100%' }}>
        <Typography.Title level={3} style={{ marginTop: 0, marginBottom: 8 }}>
          Agent Backend
        </Typography.Title>
        <Typography.Paragraph type="secondary">
          登录后浏览轨迹与设备。
        </Typography.Paragraph>
        <Form
          form={form}
          layout="vertical"
          initialValues={{ username: 'admin' }}
          onFinish={handleLogin}
        >
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input autoFocus autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={submitting} block>
            登录
          </Button>
        </Form>
      </Card>
    </div>
  )
}
