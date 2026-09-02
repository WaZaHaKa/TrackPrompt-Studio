export function formatDuration(seconds: number | null, compact = false): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return 'Not available'
  if (seconds < 60) return compact ? `${Math.round(seconds)}s` : `${Math.round(seconds)} seconds`
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.round((seconds % 3600) / 60)
  if (hours === 0) return compact ? `${minutes}m` : `${minutes} minutes`
  if (compact) return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`
  return minutes > 0 ? `About ${hours} hr ${minutes} min` : `About ${hours} hours`
}

export function formatBytes(bytes: number | null): string {
  if (bytes === null || !Number.isFinite(bytes) || bytes < 0) return 'Not available'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  const precision = value >= 100 || unit === 0 ? 0 : value >= 10 ? 1 : 2
  return `${value.toFixed(precision)} ${units[unit] ?? 'B'}`
}

export function formatGiB(value: number | null): string {
  if (value === null || !Number.isFinite(value) || value < 0) return 'Not available'
  return `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })} GiB`
}

export function formatDateTime(value: string | null): string {
  if (!value) return 'Not available'
  const date = new Date(value)
  if (Number.isNaN(date.valueOf())) return 'Not available'
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

export function formatClock(value: string | null): string {
  if (!value) return 'Calculating…'
  const date = new Date(value)
  if (Number.isNaN(date.valueOf())) return 'Calculating…'
  return new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(date)
}

export function sentenceCase(value: string): string {
  const result = value.replace(/_/g, ' ').trim()
  return result.length > 0 ? `${result[0]?.toUpperCase() ?? ''}${result.slice(1)}` : ''
}

export function shortHash(value: string | null): string {
  return value ? `${value.slice(0, 12).toUpperCase()}…` : 'Unavailable'
}

export function percent(numerator: number, denominator: number): number {
  if (!Number.isFinite(numerator) || !Number.isFinite(denominator) || denominator <= 0) return 0
  return Math.min(100, Math.max(0, (numerator / denominator) * 100))
}

export function elapsedSince(value: string | null, now: number): string {
  if (!value) return 'Not available'
  const started = new Date(value).valueOf()
  if (!Number.isFinite(started)) return 'Not available'
  return formatDuration(Math.max(0, (now - started) / 1000), true)
}
