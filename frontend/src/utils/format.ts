export function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(0)}K`
  return String(value)
}

export function formatDuration(ms: number | null | undefined): string {
  if (!ms) return '-'
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m${seconds % 60}s`
}

export function formatCurrency(value: number): string {
  if (!value) return '-'
  return `$${value.toFixed(value >= 1 ? 2 : 4)}`
}

export function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = window.URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.URL.revokeObjectURL(url)
}
