import type { TaskNode } from '../api'
import { parseApiTs, formatDuration, formatClockUtc } from './format'

const TASK_TERMINAL = new Set(['succeeded', 'failed', 'stalled'])

export { TASK_TERMINAL }

export function displayTaskName(row: { name?: string | null; id: string }): string {
  const n = row.name?.trim()
  return n ? n : `未命名 (${row.id.slice(0, 8)}…)`
}

export function hasDictContent(o: Record<string, unknown> | null | undefined): boolean {
  return o != null && typeof o === 'object' && !Array.isArray(o) && Object.keys(o).length > 0
}

export function chipClass(status: string): string {
  const s = status.toLowerCase()
  if (s.includes('success') || s === 'succeeded') return 'chip chip-ok'
  if (s.includes('fail') || s.includes('stalled')) return 'chip chip-bad'
  if (s.includes('wait') || s.includes('human')) return 'chip chip-warn'
  if (s.includes('run')) return 'chip chip-run'
  return 'chip'
}

export function stringifyCell(v: unknown): string {
  if (typeof v === 'object' && v !== null) {
    return JSON.stringify(v, null, 2)
  }
  return String(v)
}

export function nodeTiming(
  n: TaskNode,
  nowMs: number,
): { summary: string; detail?: string } {
  const t0 = parseApiTs(n.started_at)
  const t1 = parseApiTs(n.finished_at)
  const st = n.status.toLowerCase()
  if (t0 === null) return { summary: 'Not started' }
  if (t1 !== null) {
    return {
      summary: `Duration ${formatDuration(t1 - t0)}`,
      detail: `${formatClockUtc(t0)} → ${formatClockUtc(t1)}`,
    }
  }
  if (st === 'running' || st === 'waiting_human') {
    return { summary: `Running ${formatDuration(Math.max(0, nowMs - t0))}` }
  }
  return { summary: `Started ${formatClockUtc(t0)}` }
}

export function sortedNodes(nodes: TaskNode[]): TaskNode[] {
  return [...nodes].sort((a, b) => a.ordinal - b.ordinal)
}
