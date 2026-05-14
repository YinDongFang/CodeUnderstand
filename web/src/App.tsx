import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import {
  fetchLogs,
  fetchTask,
  fetchTasks,
  resolveInterrupt,
  type TaskDetail,
  type TaskNode,
  type TaskSummary,
} from './api'
import './App.css'

const POLL_MS = 2000

function chipClass(status: string): string {
  const s = status.toLowerCase()
  if (s.includes('success') || s === 'succeeded') return 'chip chip-ok'
  if (s.includes('fail') || s.includes('stalled')) return 'chip chip-bad'
  if (s.includes('wait') || s.includes('human')) return 'chip chip-warn'
  if (s.includes('run')) return 'chip chip-run'
  return 'chip'
}

function parseApiTs(iso: string | null): number | null {
  if (!iso) return null
  const t = Date.parse(iso)
  return Number.isNaN(t) ? null : t
}

function formatDuration(ms: number): string {
  if (ms < 500) return `${Math.max(0, Math.round(ms))}ms`
  const s = ms / 1000
  if (s < 60) return `${s >= 10 || s === Math.floor(s) ? Math.round(s) : s.toFixed(1)}s`
  const m = Math.floor(s / 60)
  const rs = Math.floor(s % 60)
  return `${m}m${rs.toString().padStart(2, '0')}s`
}

function formatClockUtc(ms: number): string {
  return new Date(ms).toISOString().slice(11, 19)
}

function nodeTiming(n: TaskNode, nowMs: number): { summary: string; detail?: string } {
  const t0 = parseApiTs(n.started_at)
  const t1 = parseApiTs(n.finished_at)
  const st = n.status.toLowerCase()

  if (t0 === null) {
    return { summary: '未开始' }
  }
  if (t1 !== null) {
    return {
      summary: `耗时 ${formatDuration(t1 - t0)}`,
      detail: `${formatClockUtc(t0)} → ${formatClockUtc(t1)}`,
    }
  }
  if (st === 'running' || st === 'waiting_human') {
    return { summary: `已运行 ${formatDuration(Math.max(0, nowMs - t0))}` }
  }
  return { summary: `开始 ${formatClockUtc(t0)}` }
}

function sortedNodes(nodes: TaskNode[]): TaskNode[] {
  return [...nodes].sort((a, b) => a.ordinal - b.ordinal)
}

export default function App() {
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [tasksErr, setTasksErr] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<TaskDetail | null>(null)
  const [detailErr, setDetailErr] = useState<string | null>(null)
  const [logLines, setLogLines] = useState<string[]>([])
  const logCursorRef = useRef(0)
  const [logsErr, setLogsErr] = useState<string | null>(null)
  const [resolveDraft, setResolveDraft] = useState('{}')
  const [resolveErr, setResolveErr] = useState<string | null>(null)
  const [resolveBusy, setResolveBusy] = useState(false)
  const [nowTick, setNowTick] = useState(() => Date.now())

  const detailHasActiveNode =
    detail?.nodes.some(
      (n) =>
        (n.status === 'running' || n.status === 'waiting_human') && n.started_at != null,
    ) ?? false

  useEffect(() => {
    if (!detailHasActiveNode) return
    const id = window.setInterval(() => setNowTick(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [detailHasActiveNode])

  useEffect(() => {
    let cancelled = false
    const tick = () => {
      fetchTasks()
        .then((rows) => {
          if (!cancelled) {
            setTasks(rows)
            setTasksErr(null)
          }
        })
        .catch((e: Error) => {
          if (!cancelled) setTasksErr(e.message)
        })
    }
    tick()
    const id = window.setInterval(tick, POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [])

  useEffect(() => {
    if (!selectedId) {
      setDetail(null)
      setDetailErr(null)
      return
    }
    let cancelled = false
    const tick = () => {
      fetchTask(selectedId)
        .then((d) => {
          if (!cancelled) {
            setDetail(d)
            setDetailErr(null)
          }
        })
        .catch((e: Error) => {
          if (!cancelled) setDetailErr(e.message)
        })
    }
    tick()
    const id = window.setInterval(tick, POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [selectedId])

  useEffect(() => {
    logCursorRef.current = 0
    setLogLines([])
    setLogsErr(null)
  }, [selectedId])

  useEffect(() => {
    if (!selectedId) return
    let cancelled = false
    const tick = () => {
      fetchLogs(selectedId, logCursorRef.current)
        .then((log) => {
          if (cancelled) return
          setLogsErr(null)
          if (log.lines.length) {
            setLogLines((prev) => [...prev, ...log.lines])
          }
          logCursorRef.current = log.next_cursor
        })
        .catch((e: Error) => {
          if (!cancelled) setLogsErr(e.message)
        })
    }
    tick()
    const id = window.setInterval(tick, POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [selectedId])

  useEffect(() => {
    if (detail?.interrupt) {
      setResolveDraft('{}')
      setResolveErr(null)
    }
  }, [detail?.interrupt?.seq, detail?.id])

  const onResolve = useCallback(async () => {
    if (!selectedId || !detail?.interrupt) return
    let payload: Record<string, unknown>
    try {
      payload = JSON.parse(resolveDraft) as Record<string, unknown>
      if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) {
        throw new Error('Payload must be a JSON object')
      }
    } catch (e) {
      setResolveErr(e instanceof Error ? e.message : 'Invalid JSON')
      return
    }
    setResolveBusy(true)
    setResolveErr(null)
    try {
      await resolveInterrupt(selectedId, {
        interrupt_seq: detail.interrupt.seq,
        payload,
      })
      setResolveDraft('{}')
    } catch (e) {
      setResolveErr(e instanceof Error ? e.message : String(e))
    } finally {
      setResolveBusy(false)
    }
  }, [detail?.interrupt, resolveDraft, selectedId])

  return (
    <div className="shell">
      <header className="topbar">
        <h1>wf_engine</h1>
        <span className="muted">poll /api every {POLL_MS / 1000}s</span>
      </header>
      <div className="panes">
        <aside className="pane left">
          <h2>Tasks</h2>
          {tasksErr && <p className="err">{tasksErr}</p>}
          <ul className="task-list">
            {tasks.map((t) => (
              <li key={t.id}>
                <button
                  type="button"
                  className={t.id === selectedId ? 'task-row active' : 'task-row'}
                  onClick={() => setSelectedId(t.id)}
                >
                  <span className="mono">{t.id.slice(0, 8)}…</span>
                  <span className={chipClass(t.status)}>{t.status}</span>
                  <span className="muted">{t.workflow_key}</span>
                </button>
              </li>
            ))}
          </ul>
          {!tasks.length && !tasksErr && <p className="muted">No tasks yet.</p>}
        </aside>
        <main className="pane right">
          {!selectedId && <p className="muted">Select a task.</p>}
          {selectedId && detailErr && <p className="err">{detailErr}</p>}
          {detail && (
            <>
              <h2>Task</h2>
              <dl className="meta">
                <dt>id</dt>
                <dd className="mono">{detail.id}</dd>
                <dt>workflow</dt>
                <dd>
                  {detail.workflow_key} <span className="muted">rev {detail.workflow_revision}</span>
                </dd>
                <dt>status</dt>
                <dd>
                  <span className={chipClass(detail.status)}>{detail.status}</span>
                </dd>
              </dl>
              <h3>节点（顺序）</h3>
              <div className="node-strip" role="list" aria-label="工作流节点，按执行顺序从左到右">
                {sortedNodes(detail.nodes).map((n, idx) => {
                  const timing = nodeTiming(n, nowTick)
                  const errMsg =
                    n.error && typeof n.error.message === 'string'
                      ? n.error.message
                      : n.error
                        ? JSON.stringify(n.error)
                        : null
                  return (
                    <Fragment key={`${n.ordinal}-${n.node_id}`}>
                      {idx > 0 && (
                        <span className="node-sep" aria-hidden>
                          ›
                        </span>
                      )}
                      <div className="node-card" role="listitem" title={n.zip_path ?? undefined}>
                        <div className="node-card-head">
                          <span className="node-card-ord">#{n.ordinal + 1}</span>
                          <span className="node-card-id">{n.node_id}</span>
                        </div>
                        <span className={chipClass(n.status)}>{n.status}</span>
                        <div className="node-card-time">
                          <div>{timing.summary}</div>
                          {timing.detail && <div className="node-card-time-sub">{timing.detail}</div>}
                          {errMsg && <div className="node-card-err">{errMsg}</div>}
                        </div>
                      </div>
                    </Fragment>
                  )
                })}
              </div>

              {detail.interrupt && (
                <section className="interrupt">
                  <h3>Interrupt</h3>
                  <pre className="json">{JSON.stringify(detail.interrupt, null, 2)}</pre>
                  <label className="lbl" htmlFor="payload-json">
                    Resolve payload (JSON object)
                  </label>
                  <textarea
                    id="payload-json"
                    className="textarea"
                    rows={6}
                    spellCheck={false}
                    value={resolveDraft}
                    onChange={(e) => setResolveDraft(e.target.value)}
                  />
                  {resolveErr && <p className="err">{resolveErr}</p>}
                  <button type="button" className="primary" disabled={resolveBusy} onClick={() => void onResolve()}>
                    {resolveBusy ? 'Posting…' : 'POST /interrupt/resolve'}
                  </button>
                  <p className="muted small">
                    Body: <code className="mono">{`{ interrupt_seq, payload }`}</code>
                  </p>
                </section>
              )}

              <h3>Logs</h3>
              {logsErr && <p className="err">{logsErr}</p>}
              <pre className="logs">{logLines.join('\n') || '—'}</pre>
            </>
          )}
        </main>
      </div>
    </div>
  )
}
