import { Routes, Route, useNavigate, useLocation } from 'react-router-dom'
import { Layout, Menu, Typography, Modal, Input, Form, message } from 'antd'
import {
  ApartmentOutlined,
  DashboardOutlined,
  UnorderedListOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import { useEffect, useState } from 'react'
import Dashboard from './modules/trajectory/pages/Dashboard'
import CompareDetail from './modules/trajectory/pages/CompareDetail'
import CompareList from './modules/trajectory/pages/CompareList'
import TrajectoryList from './modules/trajectory/pages/TrajectoryList'
import TrajectoryDetail from './modules/trajectory/pages/TrajectoryDetail'
import { checkAuth, login } from './modules/trajectory/services/api'

const { Header, Content } = Layout

function App() {
  const navigate = useNavigate()
  const location = useLocation()
  // 登录态由服务端 cookie 决定，不再读 localStorage（规划 §PR-0.5 第 4 条）。
  // 初始 false：先假定已登录，等 /auth/me 回来再决定是否弹框 ——
  // 避免刷新页面时登录框闪一下（cookie 有效的情况下不该出现登录框）。
  const [loginOpen, setLoginOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [form] = Form.useForm()

  useEffect(() => {
    let cancelled = false
    checkAuth().then((ok) => {
      if (!cancelled && !ok) setLoginOpen(true)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const handleLogin = async () => {
    const values = await form.validateFields()
    setSubmitting(true)
    try {
      await login(values.username, values.password)
      setLoginOpen(false)
      message.success('登录成功')
      // reload 让所有已挂载的查询用新会话重新取数
      window.location.reload()
    } catch {
      message.error('用户名或密码错误')
    } finally {
      setSubmitting(false)
    }
  }

  const selectedKey = location.pathname.startsWith('/trajectories')
    ? '/trajectories'
    : location.pathname.startsWith('/compare')
      ? '/compare'
      : location.pathname.startsWith('/settings')
        ? '/settings'
        : '/'

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', padding: '0 24px' }}>
        <Typography.Title level={4} style={{ color: '#fff', margin: '0 24px 0 0', whiteSpace: 'nowrap' }}>
          Agent Backend
        </Typography.Title>
        <Menu
          theme="dark"
          mode="horizontal"
          selectedKeys={[selectedKey]}
          onClick={({ key }) => navigate(key)}
          items={[
            { key: '/', icon: <DashboardOutlined />, label: '仪表盘' },
            { key: '/trajectories', icon: <UnorderedListOutlined />, label: '轨迹' },
            { key: '/compare', icon: <ApartmentOutlined />, label: '对比' },
            { key: '/settings', icon: <SettingOutlined />, label: '设置' },
          ]}
          style={{ flex: 1 }}
        />
      </Header>
      <Content style={{ padding: '24px', background: '#141414' }}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/trajectories" element={<TrajectoryList />} />
          <Route path="/trajectories/:sessionId" element={<TrajectoryDetail />} />
          <Route path="/compare" element={<CompareList />} />
          <Route path="/compare/:groupId" element={<CompareDetail />} />
          <Route path="/settings" element={<div style={{ color: '#fff' }}>设置页（Phase 2）</div>} />
        </Routes>
      </Content>

      <Modal
        title="登录"
        open={loginOpen}
        onOk={handleLogin}
        confirmLoading={submitting}
        closable={false}
        maskClosable={false}
        cancelButtonProps={{ style: { display: 'none' } }}
      >
        <Form form={form} layout="vertical" initialValues={{ username: 'admin' }}>
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true }]}>
            <Input.Password />
          </Form.Item>
        </Form>
      </Modal>
    </Layout>
  )
}

export default App
