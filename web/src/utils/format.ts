/** Display formatting: timestamps, wall-clock durations. */

export function formatTaskDisplayTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const normalized = /Z$|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`
  const t = Date.parse(normalized)
  if (Number.isNaN(t)) return iso
  return new Date(t).toLocaleString()
}

export function formatWallSeconds(totalSec: number): string {
  const s = Math.max(0, Math.floor(totalSec))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  const r = s % 60
  if (m < 60) return r ? `${m}m ${r}s` : `${m}m`
  const h = Math.floor(m / 60)
  const rm = m % 60
  return rm ? `${h}h ${rm}m` : `${h}h`
}

export function formatDuration(ms: number): string {
  if (ms < 500) return `${Math.max(0, Math.round(ms))}ms`
  const s = ms / 1000
  if (s < 60) return `${s >= 10 || s === Math.floor(s) ? Math.round(s) : s.toFixed(1)}s`
  const m = Math.floor(s / 60)
  const rs = Math.floor(s % 60)
  return `${m}m${rs.toString().padStart(2, '0')}s`
}

export function formatClockUtc(ms: number): string {
  return new Date(ms).toISOString().slice(11, 19)
}

export function parseApiTs(iso: string | null): number | null {
  if (!iso) return null
  const t = Date.parse(iso)
  return Number.isNaN(t) ? null : t
}
