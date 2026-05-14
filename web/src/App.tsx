import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
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
  type TaskNode,
  type TaskSummary,
  type WorkflowInfo,
} from './api'
import './App.css'

const POLL_MS = 2000

/** 终态：完成时间取 `updated_at`（末次状态变更）。 */
const TASK_TERMINAL = new Set(['succeeded', 'failed', 'stalled'])

function formatTaskDisplayTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const normalized = /Z$|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`
  const t = Date.parse(normalized)
  if (Number.isNaN(t)) return iso
  return new Date(t).toLocaleString()
}

/** Wall-clock seconds (e.g. active duration from API). */
function formatWallSeconds(totalSec: number): string {
  const s = Math.max(0, Math.floor(totalSec))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  const r = s % 60
  if (m < 60) return r ? `${m}m ${r}s` : `${m}m`
  const h = Math.floor(m / 60)
  const rm = m % 60
  return rm ? `${h}h ${rm}m` : `${h}h`
}

const LOG_NODE_MARKER = /\[\d+:[^\]]+\]/
const LOG_RUN_BEGIN = 'WF_ENGINE_RUN_BEGIN'

/** Split tail buffer into runs; each new ``WF_ENGINE_RUN_BEGIN`` starts a segment (backend ``run_once``). */
function splitLogLinesIntoRuns(lines: string[]): string[][] {
  const runs: string[][] = []
  let cur: string[] = []
  for (const line of lines) {
    if (line.includes(LOG_RUN_BEGIN) && cur.length > 0) {
      runs.push(cur)
      cur = []
    }
    cur.push(line)
  }
  if (cur.length) runs.push(cur)
  return runs
}

function logRunTitle(runLines: string[], runIndex: number, totalRuns: number): string {
  const first = runLines[0] ?? ''
  const m = first.match(/WF_ENGINE_RUN_BEGIN generation=(\d+)/)
  if (m) return `Run · worker_generation ${m[1]}`
  if (totalRuns === 1) return 'Log'
  return runIndex === 0 ? 'Legacy log (no run marker)' : `Section ${runIndex + 1}`
}

/** Group log lines by first `[ordinal:node_id]` marker per line; unprefixed lines stay in ``global``. */
function groupLogLines(lines: string[]): Array<{ title: string; lines: string[] }> {
  const out: Array<{ title: string; lines: string[] }> = []
  let curTitle = 'global'

  function ensureCur() {
    const last = out[out.length - 1]
    if (!last || last.title !== curTitle) {
      out.push({ title: curTitle, lines: [] })
    }
  }

  for (const line of lines) {
    const m = line.match(LOG_NODE_MARKER)
    const tag = m?.[0] ?? null
    if (tag) curTitle = tag
    ensureCur()
    out[out.length - 1].lines.push(line)
  }
  return out
}

function displayTaskName(row: { name?: string | null; id: string }): string {
  const n = row.name?.trim()
  return n ? n : `未命名 (${row.id.slice(0, 8)}…)`
}

/** Seeds textarea from JSON Schema `required` + `properties` so POST does not send `{}`. */
function defaultPayloadDraftFromSchema(
  schema: Record<string, unknown> | null | undefined,
): string {
  if (!schema || typeof schema !== 'object') {
    return '{}'
  }
  const req = schema.required
  const props = schema.properties as Record<string, Record<string, unknown>> | undefined
  if (!Array.isArray(req) || !props) {
    return '{}'
  }
  const o: Record<string, unknown> = {}
  for (const key of req) {
    if (typeof key !== 'string') continue
    const p = props[key]
    const t = p && typeof p === 'object' ? (p as { type?: string }).type : undefined
    if (t === 'string') o[key] = ''
    else if (t === 'number') o[key] = 0
    else if (t === 'boolean') o[key] = false
    else if (t === 'array') o[key] = []
    else if (t === 'object') o[key] = {}
    else o[key] = null
  }
  return JSON.stringify(o, null, 2)
}

type InputFieldSpec = { key: string; required: boolean; type: string; title: string }

function parseInputSchema(schema: unknown): InputFieldSpec[] | null {
  if (!schema || typeof schema !== 'object') return null
  const s = schema as Record<string, unknown>
  if (s.type !== 'object') return null
  const props = s.properties
  if (!props || typeof props !== 'object') return null
  const keys = Object.keys(props as object)
  if (keys.length === 0) return null
  const required = new Set(
    Array.isArray(s.required)
      ? (s.required as unknown[]).filter((x): x is string => typeof x === 'string')
      : [],
  )
  const po = props as Record<string, Record<string, unknown>>
  return keys.map((key) => {
    const p = po[key]
    const t = typeof p?.type === 'string' ? p.type : 'string'
    const title = typeof p?.title === 'string' ? p.title : key
    return { key, required: required.has(key), type: t, title }
  })
}

function buildInputFromForm(
  fields: InputFieldSpec[],
  values: Record<string, string>,
): Record<string, unknown> {
  const o: Record<string, unknown> = {}
  for (const f of fields) {
    const raw = values[f.key] ?? ''
    if (f.type === 'number' || f.type === 'integer') {
      if (raw.trim() === '') {
        if (f.required) throw new Error(`请填写：${f.title}`)
        continue
      }
      const n = Number(raw)
      if (Number.isNaN(n)) throw new Error(`${f.title} 须为数字`)
      o[f.key] = n
    } else if (f.type === 'boolean') {
      o[f.key] = raw === 'true' || raw === '1'
    } else {
      if (f.required && !raw.trim()) throw new Error(`请填写：${f.title}`)
      if (raw !== '' || f.required) o[f.key] = raw
    }
  }
  return o
}

function hasDictContent(o: Record<string, unknown> | null | undefined): boolean {
  return o != null && typeof o === 'object' && !Array.isArray(o) && Object.keys(o).length > 0
}

function KvBlock({ title, data }: { title: string; data: Record<string, unknown> }) {
  return (
    <>
      <h3 className="section-heading">{title}</h3>
      <table className="kv-table">
        <tbody>
          {Object.entries(data).map(([k, v]) => (
            <tr key={k}>
              <th scope="row" className="mono">
                {k}
              </th>
              <td>
                <pre className="kv-cell">{stringifyCell(v)}</pre>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

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
    return { summary: 'Not started' }
  }
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

function sortedNodes(nodes: TaskNode[]): TaskNode[] {
  return [...nodes].sort((a, b) => a.ordinal - b.ordinal)
}

function stringifyCell(v: unknown): string {
  if (typeof v === 'object' && v !== null) {
    return JSON.stringify(v, null, 2)
  }
  return String(v)
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

  const [opsCookieDraft, setOpsCookieDraft] = useState('')
  const [opsAuthDraft, setOpsAuthDraft] = useState('')
  const [tasksRootDraft, setTasksRootDraft] = useState('')
  const [serverTasksRootDefault, setServerTasksRootDefault] = useState('')
  const [tasksRootEffective, setTasksRootEffective] = useState('')
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
    setNodeActionErr(null)
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
    if (!modalOpen) return
    let cancelled = false
    setWorkflowsErr(null)
    setWorkflowsLoading(true)
    fetchWorkflows()
      .then((wfs) => {
        if (cancelled) return
        setWorkflowOptions(wfs)
        setNewWorkflowKey((prev) =>
          prev && wfs.some((w) => w.key === prev) ? prev : wfs[0]?.key ?? '',
        )
      })
      .catch((e: Error) => {
        if (!cancelled) setWorkflowsErr(e.message)
      })
      .finally(() => {
        if (!cancelled) setWorkflowsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [modalOpen])

  useEffect(() => {
    if (rightPanelTab !== 'settings') return
    let cancelled = false
    setSettingsLoadErr(null)
    setSettingsSaveErr(null)
    setSettingsBusy(true)
    fetchSettings()
      .then((s) => {
        if (cancelled) return
        setTasksRootDraft(s.tasks_root)
        setOpsCookieDraft(s.cookie)
        setOpsAuthDraft(s.authorization)
        setServerTasksRootDefault(s.server_tasks_root_default)
        setTasksRootEffective(s.tasks_root_effective)
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

  useEffect(() => {
    if (!modalOpen) return
    const wf = workflowOptions.find((w) => w.key === newWorkflowKey)
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
  }, [modalOpen, newWorkflowKey, workflowOptions])

  useEffect(() => {
    if (detail?.interrupt) {
      setResolveDraft(defaultPayloadDraftFromSchema(detail.interrupt.expected_schema))
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
      setResolveDraft(
        defaultPayloadDraftFromSchema(detail.interrupt.expected_schema),
      )
    } catch (e) {
      setResolveErr(e instanceof Error ? e.message : String(e))
    } finally {
      setResolveBusy(false)
    }
  }, [detail?.interrupt, resolveDraft, selectedId])

  const onRerunFromNode = useCallback(
    async (nodeId: string) => {
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
    },
    [detail, selectedId],
  )

  const onSaveConsoleSettings = async () => {
    setSettingsSaveErr(null)
    setSettingsBusy(true)
    try {
      const s = await saveSettings({
        tasks_root: tasksRootDraft,
        cookie: opsCookieDraft,
        authorization: opsAuthDraft,
      })
      setTasksRootDraft(s.tasks_root)
      setOpsCookieDraft(s.cookie)
      setOpsAuthDraft(s.authorization)
      setServerTasksRootDefault(s.server_tasks_root_default)
      setTasksRootEffective(s.tasks_root_effective)
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
    setNewTaskName('')
    setNewWorkflowKey('')
    setInputForm({})
    setModalOpen(true)
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
    let input: Record<string, unknown> = {}
    if (inputFields && inputFields.length > 0) {
      try {
        input = buildInputFromForm(inputFields, inputForm)
      } catch (e) {
        setCreateErr(e instanceof Error ? e.message : String(e))
        return
      }
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
      setRightPanelTab('detail')
      setSelectedId(task_id)
    } catch (e) {
      setCreateErr(e instanceof Error ? e.message : String(e))
    } finally {
      setCreateBusy(false)
    }
  }

  const logRuns = splitLogLinesIntoRuns(logLines)

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
                  className="ghost-btn btn-sm"
                  onClick={() => setRightPanelTab('settings')}
                >
                  系统设置
                </button>
                <button type="button" className="primary btn-sm" onClick={openCreateModal}>
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
                    className={t.id === selectedId ? 'task-row active' : 'task-row'}
                    onClick={() => {
                      setRightPanelTab('detail')
                      setSelectedId(t.id)
                    }}
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
            <div className="right-tabs-bar" role="tablist" aria-label="右栏视图">
              <button
                type="button"
                role="tab"
                aria-selected={rightPanelTab === 'detail'}
                className={rightPanelTab === 'detail' ? 'tab-pill is-active' : 'tab-pill'}
                onClick={() => setRightPanelTab('detail')}
              >
                任务详情
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={rightPanelTab === 'settings'}
                className={rightPanelTab === 'settings' ? 'tab-pill is-active' : 'tab-pill'}
                onClick={() => setRightPanelTab('settings')}
              >
                系统设置
              </button>
            </div>

            {rightPanelTab === 'settings' ? (
              <section className="settings-in-pane" aria-label="系统设置">
                {settingsLoadErr && <p className="err">{settingsLoadErr}</p>}
                <label className="lbl mono" htmlFor="ro-server-root">
                  server_tasks_root_default
                </label>
                <input
                  id="ro-server-root"
                  readOnly
                  className="input-text mono muted-field"
                  value={serverTasksRootDefault}
                />
                <label className="lbl mono" htmlFor="ro-effective-root">
                  tasks_root_effective
                </label>
                <input
                  id="ro-effective-root"
                  readOnly
                  className="input-text mono muted-field"
                  value={tasksRootEffective}
                />
                <label className="lbl mono" htmlFor="sys-tasks-root">
                  tasks_root
                </label>
                <textarea
                  id="sys-tasks-root"
                  className="textarea mono"
                  rows={2}
                  spellCheck={false}
                  autoComplete="off"
                  value={tasksRootDraft}
                  onChange={(e) => setTasksRootDraft(e.target.value)}
                  disabled={settingsBusy}
                />
                <label className="lbl mono" htmlFor="ops-cookie-settings">
                  cookie
                </label>
                <textarea
                  id="ops-cookie-settings"
                  className="textarea mono"
                  rows={5}
                  spellCheck={false}
                  value={opsCookieDraft}
                  onChange={(e) => setOpsCookieDraft(e.target.value)}
                  disabled={settingsBusy}
                />
                <label className="lbl mono" htmlFor="ops-auth-settings">
                  authorization
                </label>
                <textarea
                  id="ops-auth-settings"
                  className="textarea mono"
                  rows={5}
                  spellCheck={false}
                  autoComplete="off"
                  value={opsAuthDraft}
                  onChange={(e) => setOpsAuthDraft(e.target.value)}
                  disabled={settingsBusy}
                />
                {settingsSaveErr && <p className="err">{settingsSaveErr}</p>}
                <div className="settings-save-row">
                  <button
                    type="button"
                    className="primary"
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

                <h3 className="section-heading">Nodes</h3>
                {nodeActionErr && <p className="err">{nodeActionErr}</p>}
                <div
                  className="node-strip"
                  role="list"
                  aria-label="Workflow nodes in execution order, left to right"
                >
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
                            {timing.detail && (
                              <div className="node-card-time-sub">{timing.detail}</div>
                            )}
                            {errMsg && <div className="node-card-err">{errMsg}</div>}
                          </div>
                          <button
                            type="button"
                            className="ghost-btn node-rerun-btn"
                            disabled={
                              detail.status === 'waiting_human' ||
                              rerunBusyNodeId === n.node_id
                            }
                            title={
                              detail.status === 'waiting_human'
                                ? 'Cannot rerun while waiting for human input'
                                : 'Rerun from this node (POST /tasks/…/rerun)'
                            }
                            onClick={() => void onRerunFromNode(n.node_id)}
                          >
                            {rerunBusyNodeId === n.node_id ? 'Submitting…' : 'Rerun from here'}
                          </button>
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
                    <button
                      type="button"
                      className="primary"
                      disabled={resolveBusy}
                      onClick={() => void onResolve()}
                    >
                      {resolveBusy ? 'Posting…' : 'POST /interrupt/resolve'}
                    </button>
                    <p className="muted small">
                      Body: <code className="mono">{`{ interrupt_seq, payload }`}</code>
                    </p>
                  </section>
                )}

                <h3 className="section-heading">Logs</h3>
                {logsErr && <p className="err">{logsErr}</p>}
                {!logLines.length && !logsErr && <pre className="logs logs-empty">—</pre>}
                {logLines.length > 0 && (
                  <div className="log-runs-stack" aria-label="Logs by worker run">
                    {logRuns.map((runLines, ri) => {
                      const nodeGroups = groupLogLines(runLines)
                      const runTitle = logRunTitle(runLines, ri, logRuns.length)
                      const isLatest = ri === logRuns.length - 1
                      return (
                        <div
                          key={`run-${ri}-${runLines.length}-${runLines[0]?.slice(0, 48) ?? ''}`}
                          className={
                            isLatest ? 'log-run-card log-run-card-latest' : 'log-run-card'
                          }
                        >
                          <details className="log-run" open={isLatest}>
                            <summary className="log-run-summary">
                              <span className="log-run-summary-left">
                                <span className="log-run-badge">
                                  Run {ri + 1}/{logRuns.length}
                                </span>
                                <span className="log-run-title">{runTitle}</span>
                              </span>
                              <span className="muted log-summary-meta">
                                {runLines.length} lines
                              </span>
                            </summary>
                            <div className="log-run-body">
                              {nodeGroups.map((g, i) => (
                                <details
                                  key={`${ri}-${g.title}-${i}`}
                                  className="log-block"
                                  open={isLatest}
                                >
                                  <summary className="log-summary">
                                    <span className="log-summary-label">{g.title}</span>
                                    <span className="muted log-summary-meta">
                                      {g.lines.length} lines
                                    </span>
                                  </summary>
                                  <pre className="log-block-body">{g.lines.join('\n')}</pre>
                                </details>
                              ))}
                            </div>
                          </details>
                        </div>
                      )
                    })}
                  </div>
                )}
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
              <button type="button" className="ghost-btn" onClick={() => setModalOpen(false)}>
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
                onChange={(e) => setNewWorkflowKey(e.target.value)}
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
                          type={f.type === 'number' || f.type === 'integer' ? 'number' : 'text'}
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
              <button type="button" className="ghost-btn" onClick={() => setModalOpen(false)}>
                取消
              </button>
              <button
                type="button"
                className="primary"
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
