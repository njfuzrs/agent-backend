import { Routes, Route, Navigate, useNavigate, useLocation, Outlet } from 'react-router-dom'
import { Layout, Menu, Typography, Button, Space, Spin } from 'antd'
import {
  DashboardOutlined,
  DesktopOutlined,
  FlagOutlined,
  LogoutOutlined,
  SafetyCertificateOutlined,
  AuditOutlined,
  SettingOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons'
import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import Dashboard from './modules/trajectory/pages/Dashboard'
import TrajectoryList from './modules/trajectory/pages/TrajectoryList'
import TrajectoryDetail from './modules/trajectory/pages/TrajectoryDetail'
import DeviceList from './modules/identity/pages/DeviceList'
import FlagList from './modules/flag/pages/FlagList'
import PolicyList from './modules/policy/pages/PolicyList'
import AuditOverview from './modules/event/pages/AuditOverview'
import LoginPage from './pages/Login'
import { checkAuth, logout } from './modules/trajectory/services/api'

const { Header, Content } = Layout

function ProtectedLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const [ready, setReady] = useState(false)
  const [authed, setAuthed] = useState(false)
  const queryClient = useQueryClient()

  useEffect(() => {
    let cancelled = false
    checkAuth().then(ok => {
      if (cancelled) return
      setAuthed(ok)
      setReady(true)
    })
    return () => {
      cancelled = true
    }
  }, [location.pathname])

  if (!ready) {
    return <Spin size="large" style={{ display: 'block', margin: '120px auto' }} />
  }
  if (!authed) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }

  const selectedKey = location.pathname.startsWith('/trajectories')
    ? '/trajectories'
    : location.pathname.startsWith('/devices')
      ? '/devices'
      : location.pathname.startsWith('/flags')
        ? '/flags'
        : location.pathname.startsWith('/policies')
          ? '/policies'
          : location.pathname.startsWith('/audit')
            ? '/audit'
            : location.pathname.startsWith('/settings')
              ? '/settings'
              : '/'

  const handleLogout = async () => {
    try {
      await logout()
    } finally {
      queryClient.clear()
      navigate('/login', { replace: true })
    }
  }

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
            { key: '/devices', icon: <DesktopOutlined />, label: '设备' },
            { key: '/flags', icon: <FlagOutlined />, label: 'Flag' },
            { key: '/policies', icon: <SafetyCertificateOutlined />, label: '策略' },
            { key: '/audit', icon: <AuditOutlined />, label: '审计' },
            { key: '/settings', icon: <SettingOutlined />, label: '设置' },
          ]}
          style={{ flex: 1 }}
        />
        <Space>
          <Button type="text" icon={<LogoutOutlined />} onClick={handleLogout} style={{ color: '#fff' }}>
            登出
          </Button>
        </Space>
      </Header>
      <Content style={{ padding: '24px', background: '#141414' }}>
        <Outlet />
      </Content>
    </Layout>
  )
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedLayout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/trajectories" element={<TrajectoryList />} />
        <Route path="/trajectories/:sessionId" element={<TrajectoryDetail />} />
        <Route path="/devices" element={<DeviceList />} />
        <Route path="/flags" element={<FlagList />} />
        <Route path="/policies" element={<PolicyList />} />
        <Route path="/audit" element={<AuditOverview />} />
        <Route path="/settings" element={<div style={{ color: '#fff' }}>设置页（Phase 2）</div>} />
        <Route path="/compare" element={<Navigate to="/" replace />} />
        <Route path="/compare/:groupId" element={<Navigate to="/" replace />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default App
