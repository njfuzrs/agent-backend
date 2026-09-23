import { Empty } from 'antd'
import { CHART_COLORS } from '../../../utils/chart'

interface Datum {
  label: string
  value: number
}

interface DistributionDatum {
  name: string
  count: number
}

interface SeriesItem {
  name: string
  values: number[]
}

function EmptyChart() {
  return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无数据" />
}

export function LineTrendChart({ data, color = CHART_COLORS[0] }: { data: Datum[]; color?: string }) {
  if (!data.length) return <EmptyChart />

  const width = 620
  const height = 240
  const padding = 28
  const maxValue = Math.max(...data.map(item => item.value), 1)
  const stepX = data.length > 1 ? (width - padding * 2) / (data.length - 1) : 0

  const points = data.map((item, index) => {
    const x = padding + stepX * index
    const y = height - padding - (item.value / maxValue) * (height - padding * 2)
    return { ...item, x, y }
  })

  const path = points.map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x},${point.y}`).join(' ')
  const areaPath = `${path} L${points[points.length - 1]!.x},${height - padding} L${points[0]!.x},${height - padding} Z`

  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', height: 240 }}>
      <defs>
        <linearGradient id="line-area-gradient" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.35" />
          <stop offset="100%" stopColor={color} stopOpacity="0.02" />
        </linearGradient>
      </defs>
      <line x1={padding} y1={height - padding} x2={width - padding} y2={height - padding} stroke="#303030" />
      <path d={areaPath} fill="url(#line-area-gradient)" />
      <path d={path} fill="none" stroke={color} strokeWidth="3" strokeLinejoin="round" strokeLinecap="round" />
      {points.map(point => (
        <g key={point.label}>
          <circle cx={point.x} cy={point.y} r="4" fill={color} />
          <text x={point.x} y={height - 10} fill="#8c8c8c" fontSize="11" textAnchor="middle">
            {point.label}
          </text>
        </g>
      ))}
    </svg>
  )
}

export function DonutChart({ items }: { items: DistributionDatum[] }) {
  if (!items.length) return <EmptyChart />

  const total = items.reduce((sum, item) => sum + item.count, 0)
  const radius = 56
  const circumference = 2 * Math.PI * radius

  const segments = items.map(item => ({
    ...item,
    dash: (item.count / total) * circumference,
  }))

  const offsets = segments.map((_segment, index) =>
    segments.slice(0, index).reduce((sum, current) => sum + current.dash, 0)
  )

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '180px 1fr', gap: 16, alignItems: 'center' }}>
      <svg viewBox="0 0 160 160" style={{ width: '100%', maxWidth: 180 }}>
        <g transform="translate(80,80) rotate(-90)">
          <circle r={radius} fill="none" stroke="#262626" strokeWidth="18" />
          {segments.map((item, index) => {
            const dashArray = `${item.dash} ${circumference - item.dash}`
            return (
              <circle
                key={item.name}
                r={radius}
                fill="none"
                stroke={CHART_COLORS[index % CHART_COLORS.length]}
                strokeWidth="18"
                strokeDasharray={dashArray}
                strokeDashoffset={-offsets[index]!}
              />
            )
          })}
        </g>
        <text x="80" y="74" fill="#bfbfbf" fontSize="12" textAnchor="middle">
          总计
        </text>
        <text x="80" y="94" fill="#ffffff" fontSize="24" fontWeight="700" textAnchor="middle">
          {total}
        </text>
      </svg>

      <div style={{ display: 'grid', gap: 10 }}>
        {items.map((item, index) => {
          const percent = total ? ((item.count / total) * 100).toFixed(1) : '0.0'
          return (
            <div key={item.name} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span
                style={{
                  width: 10,
                  height: 10,
                  borderRadius: '50%',
                  background: CHART_COLORS[index % CHART_COLORS.length],
                  flexShrink: 0,
                }}
              />
              <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {item.name}
              </span>
              <span style={{ color: '#8c8c8c' }}>{percent}%</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function HorizontalBarChart({ items }: { items: DistributionDatum[] }) {
  if (!items.length) return <EmptyChart />

  const maxValue = Math.max(...items.map(item => item.count), 1)
  return (
    <div style={{ display: 'grid', gap: 12 }}>
      {items.map((item, index) => (
        <div key={item.name} style={{ display: 'grid', gridTemplateColumns: '120px 1fr 48px', gap: 12, alignItems: 'center' }}>
          <span style={{ color: '#d9d9d9', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {item.name}
          </span>
          <div style={{ height: 10, borderRadius: 999, background: '#262626', overflow: 'hidden' }}>
            <div
              style={{
                width: `${(item.count / maxValue) * 100}%`,
                height: '100%',
                borderRadius: 999,
                background: CHART_COLORS[index % CHART_COLORS.length],
              }}
            />
          </div>
          <span style={{ color: '#8c8c8c', textAlign: 'right' }}>{item.count}</span>
        </div>
      ))}
    </div>
  )
}

export function StackedAreaChart({ dates, series }: { dates: string[]; series: SeriesItem[] }) {
  if (!dates.length || !series.length) return <EmptyChart />

  const width = 620
  const height = 260
  const padding = 28
  const stepX = dates.length > 1 ? (width - padding * 2) / (dates.length - 1) : 0

  const stacks = dates.map((_, dateIndex) => {
    let total = 0
    return series.map(item => {
      const value = item.values[dateIndex] ?? 0
      const bottom = total
      total += value
      return { value, bottom, top: total }
    })
  })

  const maxTotal = Math.max(
    ...stacks.map(points => points[points.length - 1]?.top ?? 0),
    1
  )

  const areaPaths = series.map((item, seriesIndex) => {
    const topPoints = dates.map((_, dateIndex) => {
      const x = padding + stepX * dateIndex
      const y = height - padding - ((stacks[dateIndex]?.[seriesIndex]?.top ?? 0) / maxTotal) * (height - padding * 2)
      return { x, y }
    })
    const bottomPoints = dates.map((_, dateIndex) => {
      const x = padding + stepX * dateIndex
      const y = height - padding - ((stacks[dateIndex]?.[seriesIndex]?.bottom ?? 0) / maxTotal) * (height - padding * 2)
      return { x, y }
    }).reverse()

    const path = [
      topPoints.map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x},${point.y}`).join(' '),
      ...bottomPoints.map(point => `L${point.x},${point.y}`),
      'Z',
    ].join(' ')

    return { name: item.name, path, color: CHART_COLORS[seriesIndex % CHART_COLORS.length] }
  })

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <svg viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', height: 260 }}>
        <line x1={padding} y1={height - padding} x2={width - padding} y2={height - padding} stroke="#303030" />
        {areaPaths.map(area => (
          <path key={area.name} d={area.path} fill={area.color} opacity="0.45" stroke={area.color} strokeWidth="1.5" />
        ))}
        {dates.map((label, index) => (
          <text
            key={label}
            x={padding + stepX * index}
            y={height - 8}
            fill="#8c8c8c"
            fontSize="11"
            textAnchor="middle"
          >
            {label}
          </text>
        ))}
      </svg>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
        {series.map((item, index) => (
          <div key={item.name} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span
              style={{
                width: 10,
                height: 10,
                borderRadius: '50%',
                background: CHART_COLORS[index % CHART_COLORS.length],
              }}
            />
            <span style={{ color: '#bfbfbf' }}>{item.name}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export function DailyBarChart({ data }: { data: Datum[] }) {
  if (!data.length) return <EmptyChart />

  const maxValue = Math.max(...data.map(item => item.value), 1)
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 8, height: 168, paddingTop: 8 }}>
      {data.map(item => (
        <div
          key={item.label}
          style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 6,
            minWidth: 0,
          }}
        >
          <span style={{ color: '#8c8c8c', fontSize: 12 }}>{item.value}</span>
          <div
            style={{
              width: '100%',
              maxWidth: 36,
              height: Math.max((item.value / maxValue) * 120, item.value > 0 ? 4 : 0),
              background: CHART_COLORS[0],
              borderRadius: 4,
            }}
          />
          <span
            style={{
              color: '#8c8c8c',
              fontSize: 11,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              width: '100%',
              textAlign: 'center',
            }}
          >
            {item.label}
          </span>
        </div>
      ))}
    </div>
  )
}

