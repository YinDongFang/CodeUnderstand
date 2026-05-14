import { useEffect, useRef, useState } from 'react'
import {
  fetchLogs,
  fetchTask,
  fetchTasks,
  resolveInterrupt,
  rerunTask,
  type TaskDetail as TaskDetailType,
  type TaskSummary,
} from './api'
import { defaultPayloadDraftFromSchema } from './utils/schema'
import TaskList from './components/TaskList'
import TaskDetail from './components/TaskDetail'
import SettingsPanel from './components/SettingsPanel'
import CreateTaskModal from './components/CreateTaskModal'
import './App.css'

const POLL_MS = 2000

export default function App() {
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [tasksErr, setTasksErr] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<TaskDetailType | null>(null)
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
          <TaskList
            tasks={tasks}
            tasksErr={tasksErr}
            selectedId={selectedId}
            rightPanelTab={rightPanelTab}
            onSelectTask={selectTask}
            onOpenSettings={() => setRightPanelTab('settings')}
            onOpenCreate={() => setModalOpen(true)}
          />
          <main className="pane right">
            {rightPanelTab === 'settings' ? (
              <SettingsPanel />
            ) : !selectedId ? (
              <p className="muted">请从左侧选择任务。</p>
            ) : detail ? (
              <TaskDetail
                detail={detail}
                detailErr={detailErr}
                nowTick={nowTick}
                nodeActionErr={nodeActionErr}
                rerunBusyNodeId={rerunBusyNodeId}
                resolveDraft={resolveDraft}
                resolveErr={resolveErr}
                resolveBusy={resolveBusy}
                logLines={logLines}
                logsErr={logsErr}
                onRerun={onRerunFromNode}
                onResolve={onResolve}
                onResolveDraftChange={setResolveDraft}
              />
            ) : (
              selectedId && detailErr && <p className="err">{detailErr}</p>
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
