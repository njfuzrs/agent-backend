import { Routes, Route, useNavigate, useLocation } from 'react-router-dom'
import { Layout, Menu, Typography, Modal, Input, Form, message } from 'antd'
import {
  DashboardOutlined,
  UnorderedListOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import { useState } from 'react'
import Dashboard from './pages/Dashboard'
import TrajectoryList from './pages/TrajectoryList'
import TrajectoryDetail from './pages/TrajectoryDetail'
import { setAuth, hasAuth } from './services/api'

const { Header, Content } = Layout

function App() {
  const navigate = useNavigate()
  const location = useLocation()
  const [loginOpen, setLoginOpen] = useState(!hasAuth())
  const [form] = Form.useForm()

  const handleLogin = async () => {
    const values = await form.validateFields()
    setAuth(values.username, values.password)
    setLoginOpen(false)
    message.success('登录成功')
    window.location.reload()
  }

  const selectedKey = location.pathname.startsWith('/trajectories') ? '/trajectories' : '/'

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', padding: '0 24px' }}>
        <Typography.Title level={4} style={{ color: '#fff', margin: '0 24px 0 0', whiteSpace: 'nowrap' }}>
          Trajectory Platform
        </Typography.Title>
        <Menu
          theme="dark"
          mode="horizontal"
          selectedKeys={[selectedKey]}
          onClick={({ key }) => navigate(key)}
          items={[
            { key: '/', icon: <DashboardOutlined />, label: '仪表盘' },
            { key: '/trajectories', icon: <UnorderedListOutlined />, label: '轨迹' },
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
          <Route path="/settings" element={<div style={{ color: '#fff' }}>设置页（Phase 2）</div>} />
        </Routes>
      </Content>

      <Modal
        title="登录"
        open={loginOpen}
        onOk={handleLogin}
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
