import { Routes, Route, Navigate, useNavigate, useLocation, Outlet } from 'react-router-dom'
import { Layout, Menu, Typography, Button, Space, Spin, Result } from 'antd'
import {
  ApiOutlined,
  DashboardOutlined,
  DesktopOutlined,
  DollarOutlined,
  FlagOutlined,
  KeyOutlined,
  LogoutOutlined,
  SafetyCertificateOutlined,
  AuditOutlined,
  AppstoreOutlined,
  SettingOutlined,
  TeamOutlined,
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
import CostOverview from './modules/cost/pages/CostOverview'
import BridgeSessions from './modules/bridge/pages/BridgeSessions'
import UserList from './modules/auth/pages/UserList'
import MarketList from './modules/marketplace/pages/MarketList'
import FeishuDelegation from './modules/feishu/pages/FeishuDelegation'
import LoginPage from './pages/Login'
import { fetchMe, logout, type MeResponse } from './modules/trajectory/services/api'

const { Header, Content } = Layout

function ProtectedLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const [ready, setReady] = useState(false)
  const [me, setMe] = useState<MeResponse | null>(null)
  const queryClient = useQueryClient()

  useEffect(() => {
    let cancelled = false
    fetchMe().then(result => {
      if (cancelled) return
      setMe(result)
      setReady(true)
    })
    return () => {
      cancelled = true
    }
  }, [location.pathname])

  if (!ready) {
    return <Spin size="large" style={{ display: 'block', margin: '120px auto' }} />
  }
  if (!me) {
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
            : location.pathname.startsWith('/cost')
              ? '/cost'
              : location.pathname.startsWith('/bridge')
                ? '/bridge'
                : location.pathname.startsWith('/users')
                ? '/users'
                : location.pathname.startsWith('/marketplace')
                ? '/marketplace'
                : location.pathname.startsWith('/feishu')
                ? '/feishu'
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

  // 飞书登录的 member：有身份但不是管理员。后端所有管理接口都会 403，这里直接说清楚。
  if (!me.is_admin) {
    return (
      <Result
        status="403"
        title="没有管理台权限"
        subTitle={`已登录为 ${me.name || me.username}。管理台只对管理员开放，需要权限请联系现有管理员。`}
        extra={<Button onClick={handleLogout}>退出登录</Button>}
        style={{ minHeight: '100vh', background: '#141414', paddingTop: 120 }}
      />
    )
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
            { key: '/cost', icon: <DollarOutlined />, label: '成本' },
            { key: '/bridge', icon: <ApiOutlined />, label: '遥控' },
            { key: '/marketplace', icon: <AppstoreOutlined />, label: '市场' },
            { key: '/users', icon: <TeamOutlined />, label: '用户' },
            { key: '/feishu', icon: <KeyOutlined />, label: '飞书授权' },
            { key: '/settings', icon: <SettingOutlined />, label: '设置' },
          ]}
          style={{ flex: 1 }}
        />
        <Space>
          <Typography.Text style={{ color: 'rgba(255,255,255,0.65)', whiteSpace: 'nowrap' }}>
            {me.kind === 'password' ? '口令登录（应急）' : me.name || me.username}
          </Typography.Text>
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
        <Route path="/cost" element={<CostOverview />} />
        <Route path="/bridge" element={<BridgeSessions />} />
        <Route path="/users" element={<UserList />} />
        <Route path="/marketplace" element={<MarketList />} />
        <Route path="/feishu" element={<FeishuDelegation />} />
        <Route path="/settings" element={<div style={{ color: '#fff' }}>设置页（Phase 2）</div>} />
        <Route path="/compare" element={<Navigate to="/" replace />} />
        <Route path="/compare/:groupId" element={<Navigate to="/" replace />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default App
