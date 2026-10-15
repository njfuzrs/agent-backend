import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfigProvider, theme } from 'antd'
import App from './App'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ConfigProvider
        theme={{
          algorithm: theme.darkAlgorithm,
          token: {
            colorPrimary: '#1668dc',
            borderRadius: 6,
          },
          // 顶栏与暗色菜单必须同色：antd 默认 Header / dark Menu 都是 #001529（深蓝），
          // 这里统一成 #1f1f1f，避免菜单区和两侧标题 / 登出区颜色不一致。
          components: {
            Layout: { headerBg: '#1f1f1f' },
            Menu: { darkItemBg: '#1f1f1f', darkPopupBg: '#1f1f1f' },
          },
        }}
      >
        <BrowserRouter basename="/traj">
          <App />
        </BrowserRouter>
      </ConfigProvider>
    </QueryClientProvider>
  </StrictMode>,
)
