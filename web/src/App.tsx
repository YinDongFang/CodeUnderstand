import { useEffect, useRef, useState } from 'react'
import {
  fetchLogs,
  fetchTask,
  fetchTasks,
  resolveInterrupt,
  rerunTask,
  type TaskDetail,
  type TaskSummary,
} from './api'
import { formatTaskDisplayTime, formatWallSeconds } from './utils/format'
import { defaultPayloadDraftFromSchema } from './utils/schema'
import { displayTaskName, hasDictContent, chipClass, TASK_TERMINAL } from './utils/display'
import KvBlock from './components/KvBlock'
import NodeStrip from './components/NodeStrip'
import LogViewer from './components/LogViewer'
import InterruptPanel from './components/InterruptPanel'
import SettingsPanel from './components/SettingsPanel'
import CreateTaskModal from './components/CreateTaskModal'
import './App.css'

const POLL_MS = 2000

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
  const activeInterruptRef = useRef<string | null>(null)
  const [nowTick, setNowTick] = useState(() => Date.now())

  const [modalOpen, setModalOpen] = useState(false)
  const [rightPanelTab, setRightPanelTab] = useState<'detail' | 'settings'>('detail')
  const [rerunBusyNodeId, setRerunBusyNodeId] = useState<string | null>(null)
  const [nodeActionErr, setNodeActionErr] = useState<string | null>(null)

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
    if (!selectedId) return
    let cancelled = false
    const tick = () => {
      fetchTask(selectedId)
        .then((d) => {
          if (!cancelled) {
            setDetail(d)
            setDetailErr(null)
            if (d.interrupt) {
              const interruptKey = `${d.id}:${d.interrupt.seq}`
              if (activeInterruptRef.current !== interruptKey) {
                activeInterruptRef.current = interruptKey
                setResolveDraft(defaultPayloadDraftFromSchema(d.interrupt.expected_schema))
                setResolveErr(null)
              }
            } else {
              activeInterruptRef.current = null
            }
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

  const onResolve = async () => {
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
      setResolveDraft(
        defaultPayloadDraftFromSchema(detail.interrupt.expected_schema),
      )
    } catch (e) {
      setResolveErr(e instanceof Error ? e.message : String(e))
    } finally {
      setResolveBusy(false)
    }
  }

  const onRerunFromNode = async (nodeId: string) => {
    if (!selectedId || !detail) return
    if (
      !window.confirm(
        `Rerun from node "${nodeId}"? This resets this node and all following nodes and restores prior snapshots.`,
      )
    ) {
      return
    }
    setNodeActionErr(null)
    setRerunBusyNodeId(nodeId)
    try {
      await rerunTask(selectedId, nodeId)
      logCursorRef.current = 0
      setLogLines([])
      setLogsErr(null)
      setDetail(await fetchTask(selectedId))
      const rows = await fetchTasks()
      setTasks(rows)
      setTasksErr(null)
    } catch (e) {
      setNodeActionErr(e instanceof Error ? e.message : String(e))
    } finally {
      setRerunBusyNodeId(null)
    }
  }

  const selectTask = (taskId: string) => {
    logCursorRef.current = 0
    activeInterruptRef.current = null
    setDetail(null)
    setDetailErr(null)
    setLogLines([])
    setLogsErr(null)
    setNodeActionErr(null)
    setRightPanelTab('detail')
    setSelectedId(taskId)
  }

  const openSettings = () => {
    setRightPanelTab('settings')
  }

  const handleTaskCreated = async (taskId: string) => {
    const rows = await fetchTasks()
    setTasks(rows)
    setTasksErr(null)
    selectTask(taskId)
  }

  return (
    <>
      <div className="shell">
        <div className="panes">
          <aside className="pane left">
            <div className="left-head">
              <h2 className="left-title">任务</h2>
              <div className="left-head-actions">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={openSettings}
                >
                  系统设置
                </button>
                <button type="button" className="btn btn-primary" onClick={() => setModalOpen(true)}>
                  新建任务
                </button>
              </div>
            </div>
            {tasksErr && <p className="err">{tasksErr}</p>}
            <ul className="task-list">
              {tasks.map((t) => (
                <li key={t.id}>
                  <button
                    type="button"
                    className={
                      t.id === selectedId && rightPanelTab === 'detail'
                        ? 'task-row active'
                        : 'task-row'
                    }
                    onClick={() => selectTask(t.id)}
                    title={`${t.workflow_key} · ${t.id} · Run ${t.execution_count ?? 0} · ${formatWallSeconds(t.active_duration_seconds ?? 0)} active (excl. interrupt)`}
                  >
                    <span className="task-row-main">
                      <span className="task-row-name">{displayTaskName(t)}</span>
                      <span className={chipClass(t.status)}>{t.status}</span>
                    </span>
                    <span className="task-row-meta muted small">
                      Run {t.execution_count ?? 0} · {formatWallSeconds(t.active_duration_seconds ?? 0)}{' '}
                      active
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {!tasks.length && !tasksErr && <p className="muted">暂无任务</p>}
          </aside>
          <main className="pane right">
            {rightPanelTab === 'settings' ? (
              <SettingsPanel />
            ) : (
              <>
                {!selectedId && <p className="muted">请从左侧选择任务。</p>}
                {selectedId && detailErr && <p className="err">{detailErr}</p>}
                {detail && (
              <>
                <h2 className="task-detail-title">{displayTaskName(detail)}</h2>
                <dl className="meta">
                  <dt>Id</dt>
                  <dd className="mono">{detail.id}</dd>
                  <dt>Workflow</dt>
                  <dd>
                    {detail.workflow_key}{' '}
                    <span className="muted">rev {detail.workflow_revision}</span>
                  </dd>
                  <dt>Status</dt>
                  <dd>
                    <span className={chipClass(detail.status)}>{detail.status}</span>
                  </dd>
                  <dt>Created at</dt>
                  <dd>{formatTaskDisplayTime(detail.created_at)}</dd>
                  <dt>Completed at</dt>
                  <dd>
                    {TASK_TERMINAL.has(detail.status)
                      ? formatTaskDisplayTime(detail.updated_at)
                      : '—'}
                  </dd>
                  <dt>Execution count</dt>
                  <dd>{detail.execution_count ?? 0}</dd>
                  <dt>Active duration</dt>
                  <dd title="Wall time excluding waiting_human (interrupt)">
                    {formatWallSeconds(detail.active_duration_seconds ?? 0)} (excl. interrupt)
                  </dd>
                </dl>

                {hasDictContent(detail.input) && <KvBlock title="Input" data={detail.input} />}

                {hasDictContent(detail.context) && (
                  <KvBlock title="Context" data={detail.context} />
                )}

                <NodeStrip
                  nodes={detail.nodes}
                  nowTick={nowTick}
                  taskStatus={detail.status}
                  rerunBusyNodeId={rerunBusyNodeId}
                  nodeActionErr={nodeActionErr}
                  onRerun={onRerunFromNode}
                />

                {detail.interrupt && (
                  <InterruptPanel
                    interrupt={detail.interrupt}
                    resolveDraft={resolveDraft}
                    resolveErr={resolveErr}
                    resolveBusy={resolveBusy}
                    onResolveDraftChange={setResolveDraft}
                    onResolve={onResolve}
                  />
                )}

                <LogViewer logLines={logLines} logsErr={logsErr} />
              </>
                )}
              </>
            )}
          </main>
        </div>
      </div>

      <CreateTaskModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onTaskCreated={handleTaskCreated}
      />
    </>
  )
}
