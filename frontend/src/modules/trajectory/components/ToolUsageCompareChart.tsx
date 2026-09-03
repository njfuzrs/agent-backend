import { Empty } from 'antd'
import { CHART_COLORS } from '../../../utils/chart'

interface ToolUsageSeries {
  name: string
  values: number[]
}

interface ToolUsageCompareChartProps {
  categories: string[]
  series: ToolUsageSeries[]
}

export default function ToolUsageCompareChart({ categories, series }: ToolUsageCompareChartProps) {
  if (!categories.length || !series.length) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无工具调用数据" />
  }

  const maxValue = Math.max(
    ...series.flatMap(item => item.values),
    1
  )

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      {categories.map((category, categoryIndex) => (
        <div key={category} style={{ display: 'grid', gap: 10 }}>
          <div style={{ color: '#f0f0f0', fontWeight: 600 }}>{category}</div>
          <div style={{ display: 'grid', gap: 8 }}>
            {series.map((item, seriesIndex) => {
              const value = item.values[categoryIndex] ?? 0
              return (
                <div
                  key={`${category}-${item.name}`}
                  style={{
                    display: 'grid',
                    gridTemplateColumns: '120px 1fr 40px',
                    gap: 12,
                    alignItems: 'center',
                  }}
                >
                  <span style={{ color: CHART_COLORS[seriesIndex % CHART_COLORS.length] }}>{item.name}</span>
                  <div style={{ height: 10, borderRadius: 999, background: '#262626', overflow: 'hidden' }}>
                    <div
                      style={{
                        width: `${(value / maxValue) * 100}%`,
                        height: '100%',
                        borderRadius: 999,
                        background: CHART_COLORS[seriesIndex % CHART_COLORS.length],
                      }}
                    />
                  </div>
                  <span style={{ color: '#8c8c8c', textAlign: 'right' }}>{value}</span>
                </div>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}
