import { useEffect, useRef, useState } from 'react'
import {
  createTask,
  fetchLogs,
  fetchSettings,
  fetchTask,
  fetchTasks,
  fetchWorkflows,
  resolveInterrupt,
  rerunTask,
  saveSettings,
  type TaskDetail,
  type TaskSummary,
  type WorkflowInfo,
} from './api'
import { formatTaskDisplayTime, formatWallSeconds } from './utils/format'
import { defaultPayloadDraftFromSchema, parseInputSchema, buildInputFromForm } from './utils/schema'
import { displayTaskName, hasDictContent, chipClass, TASK_TERMINAL } from './utils/display'
import KvBlock from './components/KvBlock'
import NodeStrip from './components/NodeStrip'
import LogViewer from './components/LogViewer'
import InterruptPanel from './components/InterruptPanel'
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
  const [workflowsLoading, setWorkflowsLoading] = useState(false)
  const [workflowsErr, setWorkflowsErr] = useState<string | null>(null)
  const [workflowOptions, setWorkflowOptions] = useState<WorkflowInfo[]>([])
  const [newTaskName, setNewTaskName] = useState('')
  const [newWorkflowKey, setNewWorkflowKey] = useState('')
  const [inputForm, setInputForm] = useState<Record<string, string>>({})
  const [createErr, setCreateErr] = useState<string | null>(null)
  const [createBusy, setCreateBusy] = useState(false)
  const [rerunBusyNodeId, setRerunBusyNodeId] = useState<string | null>(null)
  const [nodeActionErr, setNodeActionErr] = useState<string | null>(null)

  const opsCookieRef = useRef('')
  const opsAuthRef = useRef('')
  const [tasksRootDraft, setTasksRootDraft] = useState('')
  const [settingsLoadErr, setSettingsLoadErr] = useState<string | null>(null)
  const [settingsSaveErr, setSettingsSaveErr] = useState<string | null>(null)
  const [settingsBusy, setSettingsBusy] = useState(false)

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

  useEffect(() => {
    if (rightPanelTab !== 'settings') return
    let cancelled = false
    fetchSettings()
      .then((s) => {
        if (cancelled) return
        setTasksRootDraft(s.root)
        opsCookieRef.current = s.cookie
        opsAuthRef.current = s.authorization
      })
      .catch((e: Error) => {
        if (!cancelled) setSettingsLoadErr(e.message)
      })
      .finally(() => {
        if (!cancelled) setSettingsBusy(false)
      })
    return () => {
      cancelled = true
    }
  }, [rightPanelTab])

  const applyInputDefaults = (wf: WorkflowInfo | null | undefined) => {
    const fields = parseInputSchema(wf?.input_schema ?? null)
    if (!fields) {
      setInputForm({})
      return
    }
    const next: Record<string, string> = {}
    for (const f of fields) {
      next[f.key] = f.type === 'boolean' ? 'false' : ''
    }
    setInputForm(next)
  }

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

  const onSaveConsoleSettings = async () => {
    setSettingsSaveErr(null)
    setSettingsBusy(true)
    try {
      const s = await saveSettings({
        root: tasksRootDraft,
        cookie: opsCookieRef.current,
        authorization: opsAuthRef.current,
      })
      setTasksRootDraft(s.root)
      opsCookieRef.current = s.cookie
      opsAuthRef.current = s.authorization
    } catch (e) {
      setSettingsSaveErr(e instanceof Error ? e.message : String(e))
    } finally {
      setSettingsBusy(false)
    }
  }

  const selectedWorkflow = workflowOptions.find((w) => w.key === newWorkflowKey)
  const inputFields = parseInputSchema(selectedWorkflow?.input_schema ?? null)

  const openCreateModal = () => {
    setCreateErr(null)
    setWorkflowsErr(null)
    setWorkflowsLoading(true)
    setNewTaskName('')
    setNewWorkflowKey('')
    setInputForm({})
    setModalOpen(true)
    fetchWorkflows()
      .then((wfs) => {
        setWorkflowOptions(wfs)
        const nextWorkflow = wfs[0] ?? null
        setNewWorkflowKey(nextWorkflow?.key ?? '')
        applyInputDefaults(nextWorkflow)
      })
      .catch((e: Error) => {
        setWorkflowsErr(e.message)
      })
      .finally(() => {
        setWorkflowsLoading(false)
      })
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
    setSettingsLoadErr(null)
    setSettingsSaveErr(null)
    setSettingsBusy(true)
    setRightPanelTab('settings')
  }

  const onSubmitNewTask = async () => {
    const nameTrim = newTaskName.trim()
    if (!nameTrim) {
      setCreateErr('请填写名称')
      return
    }
    if (!newWorkflowKey) {
      setCreateErr('请选择工作流')
      return
    }
    setCreateErr(null)
    let input: Record<string, unknown>
    try {
      input =
        inputFields && inputFields.length > 0
          ? buildInputFromForm(inputFields, inputForm)
          : {}
    } catch (e) {
      setCreateErr(e instanceof Error ? e.message : String(e))
      return
    }

    setCreateBusy(true)
    try {
      const { task_id } = await createTask({
        workflow_key: newWorkflowKey,
        name: nameTrim,
        input,
      })
      const rows = await fetchTasks()
      setTasks(rows)
      setTasksErr(null)
      setModalOpen(false)
      selectTask(task_id)
    } catch (e) {
      setCreateErr(e instanceof Error ? e.message : String(e))
    } finally {
      setCreateBusy(false)
    }
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
                <button type="button" className="btn btn-primary" onClick={openCreateModal}>
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
              <section className="settings-in-pane" aria-label="系统设置">
                {settingsLoadErr && <p className="err">{settingsLoadErr}</p>}
                <label className="lbl mono" htmlFor="sys-root">
                  root
                </label>
                <textarea
                  id="sys-root"
                  className="textarea mono"
                  rows={2}
                  spellCheck={false}
                  autoComplete="off"
                  value={tasksRootDraft}
                  onChange={(e) => setTasksRootDraft(e.target.value)}
                  disabled={settingsBusy}
                />
                {settingsSaveErr && <p className="err">{settingsSaveErr}</p>}
                <div className="settings-save-row">
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={settingsBusy}
                    onClick={() => void onSaveConsoleSettings()}
                  >
                    {settingsBusy ? '保存中…' : '保存'}
                  </button>
                </div>
              </section>
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

      {modalOpen && (
        <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="新建任务">
          <div className="modal">
            <header className="modal-header">
              <h3>新建任务</h3>
              <button type="button" className="btn btn-secondary" onClick={() => setModalOpen(false)}>
                关闭
              </button>
            </header>
            <div className="modal-body">
              {workflowsLoading && <p className="muted small">加载工作流…</p>}
              {workflowsErr && <p className="err">{workflowsErr}</p>}
              <label className="lbl" htmlFor="nt-name">
                名称（必填）
              </label>
              <input
                id="nt-name"
                type="text"
                className="input-text"
                value={newTaskName}
                onChange={(e) => setNewTaskName(e.target.value)}
                autoComplete="off"
              />
              <label className="lbl" htmlFor="nt-workflow">
                Workflow
              </label>
              <select
                id="nt-workflow"
                className="select-field"
                value={newWorkflowKey}
                disabled={workflowsLoading || workflowOptions.length === 0}
                onChange={(e) => {
                  const key = e.target.value
                  setNewWorkflowKey(key)
                  applyInputDefaults(workflowOptions.find((w) => w.key === key) ?? null)
                }}
              >
                {!workflowOptions.length && !workflowsLoading && (
                  <option value="">暂无工作流</option>
                )}
                {workflowOptions.map((w) => (
                  <option key={`${w.key}@${w.revision}`} value={w.key}>
                    {w.key} (rev {w.revision})
                  </option>
                ))}
              </select>
              {inputFields && inputFields.length > 0 && (
                <div className="modal-input-fields">
                  <p className="lbl strong">工作流参数 (input)</p>
                  {inputFields.map((f) => (
                    <div key={f.key} className="field-row">
                      <label className="lbl" htmlFor={`nt-in-${f.key}`}>
                        {f.title}
                        {f.required ? <span className="req-mark"> *</span> : null}
                      </label>
                      {f.type === 'boolean' ? (
                        <label className="check-row">
                          <input
                            id={`nt-in-${f.key}`}
                            type="checkbox"
                            checked={inputForm[f.key] === 'true'}
                            onChange={(e) =>
                              setInputForm((prev) => ({
                                ...prev,
                                [f.key]: e.target.checked ? 'true' : 'false',
                              }))
                            }
                          />
                          <span className="muted small">是 / 否</span>
                        </label>
                      ) : (
                        <input
                          id={`nt-in-${f.key}`}
                          type={
                            f.type === 'number' || f.type === 'integer' ? 'number' : 'text'
                          }
                          className="input-text"
                          value={inputForm[f.key] ?? ''}
                          onChange={(e) =>
                            setInputForm((prev) => ({ ...prev, [f.key]: e.target.value }))
                          }
                          autoComplete="off"
                        />
                      )}
                    </div>
                  ))}
                </div>
              )}
              {createErr && <p className="err">{createErr}</p>}
            </div>
            <footer className="modal-footer">
              <button type="button" className="btn btn-secondary" onClick={() => setModalOpen(false)}>
                取消
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={createBusy || workflowsLoading}
                onClick={() => void onSubmitNewTask()}
              >
                {createBusy ? '创建中…' : '创建'}
              </button>
            </footer>
          </div>
        </div>
      )}
    </>
  )
}
