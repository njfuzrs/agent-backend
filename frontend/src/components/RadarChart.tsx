import { Empty } from 'antd'
import { CHART_COLORS } from '../utils/chart'

interface RadarSeries {
  name: string
  values: number[]
}

interface RadarChartProps {
  dimensions: string[]
  items: RadarSeries[]
}

function polarToCartesian(cx: number, cy: number, radius: number, angle: number) {
  return {
    x: cx + radius * Math.cos(angle),
    y: cy + radius * Math.sin(angle),
  }
}

export default function RadarChart({ dimensions, items }: RadarChartProps) {
  if (!dimensions.length || !items.length) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无对比数据" />
  }

  const width = 420
  const height = 330
  const cx = width / 2
  const cy = height / 2 - 8
  const radius = 120
  const axisCount = dimensions.length
  const levels = 4

  const gridPolygons = Array.from({ length: levels }, (_, levelIndex) => {
    const currentRadius = (radius * (levelIndex + 1)) / levels
    return dimensions.map((_, index) => {
      const angle = -Math.PI / 2 + (index * Math.PI * 2) / axisCount
      const point = polarToCartesian(cx, cy, currentRadius, angle)
      return `${point.x},${point.y}`
    }).join(' ')
  })

  const axisPoints = dimensions.map((label, index) => {
    const angle = -Math.PI / 2 + (index * Math.PI * 2) / axisCount
    const inner = polarToCartesian(cx, cy, radius, angle)
    const labelPoint = polarToCartesian(cx, cy, radius + 24, angle)
    return { label, inner, labelPoint }
  })

  const series = items.map((item, index) => {
    const color = CHART_COLORS[index % CHART_COLORS.length]
    const points = item.values.map((value, valueIndex) => {
      const angle = -Math.PI / 2 + (valueIndex * Math.PI * 2) / axisCount
      const point = polarToCartesian(cx, cy, radius * value, angle)
      return `${point.x},${point.y}`
    })

    return {
      ...item,
      color,
      polygon: points.join(' '),
    }
  })

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <svg viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', maxWidth: 520, margin: '0 auto' }}>
        {gridPolygons.map(polygon => (
          <polygon
            key={polygon}
            points={polygon}
            fill="none"
            stroke="#303030"
            strokeWidth="1"
          />
        ))}

        {axisPoints.map(axis => (
          <g key={axis.label}>
            <line x1={cx} y1={cy} x2={axis.inner.x} y2={axis.inner.y} stroke="#303030" strokeWidth="1" />
            <text
              x={axis.labelPoint.x}
              y={axis.labelPoint.y}
              fill="#bfbfbf"
              fontSize="12"
              textAnchor="middle"
              dominantBaseline="central"
            >
              {axis.label}
            </text>
          </g>
        ))}

        {series.map(item => (
          <g key={item.name}>
            <polygon points={item.polygon} fill={item.color} fillOpacity="0.18" stroke={item.color} strokeWidth="2" />
            {item.values.map((value, index) => {
              const angle = -Math.PI / 2 + (index * Math.PI * 2) / axisCount
              const point = polarToCartesian(cx, cy, radius * value, angle)
              return <circle key={`${item.name}-${index}`} cx={point.x} cy={point.y} r="4" fill={item.color} />
            })}
          </g>
        ))}
      </svg>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, justifyContent: 'center' }}>
        {series.map(item => (
          <div key={item.name} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span
              style={{
                width: 10,
                height: 10,
                borderRadius: '50%',
                background: item.color,
              }}
            />
            <span style={{ color: '#d9d9d9' }}>{item.name}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
