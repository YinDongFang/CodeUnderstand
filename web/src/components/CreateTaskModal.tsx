import { useEffect, useState } from 'react'
import type { WorkflowInfo } from '../api'
import { fetchWorkflows, createTask } from '../api'
import { parseInputSchema, buildInputFromForm } from '../utils/schema'

interface CreateTaskModalProps {
  open: boolean
  onClose: () => void
  onTaskCreated: (taskId: string) => void
}

export default function CreateTaskModal({ open, onClose, onTaskCreated }: CreateTaskModalProps) {
  const [workflowsLoading, setWorkflowsLoading] = useState(false)
  const [workflowsErr, setWorkflowsErr] = useState<string | null>(null)
  const [workflowOptions, setWorkflowOptions] = useState<WorkflowInfo[]>([])
  const [newTaskName, setNewTaskName] = useState('')
  const [newWorkflowKey, setNewWorkflowKey] = useState('')
  const [inputForm, setInputForm] = useState<Record<string, string>>({})
  const [createErr, setCreateErr] = useState<string | null>(null)
  const [createBusy, setCreateBusy] = useState(false)

  const selectedWorkflow = workflowOptions.find((w) => w.key === newWorkflowKey)
  const inputFields = parseInputSchema(selectedWorkflow?.input_schema ?? null)

  useEffect(() => {
    if (!open) return
    setCreateErr(null)
    setWorkflowsErr(null)
    setWorkflowsLoading(true)
    setNewTaskName('')
    setNewWorkflowKey('')
    setInputForm({})
    fetchWorkflows()
      .then((wfs) => {
        setWorkflowOptions(wfs)
        const next = wfs[0] ?? null
        setNewWorkflowKey(next?.key ?? '')
        applyInputDefaults(next, setInputForm)
      })
      .catch((e: Error) => setWorkflowsErr(e.message))
      .finally(() => setWorkflowsLoading(false))
  }, [open])

  const onSubmit = async () => {
    const nameTrim = newTaskName.trim()
    if (!nameTrim) { setCreateErr('请填写名称'); return }
    if (!newWorkflowKey) { setCreateErr('请选择工作流'); return }
    setCreateErr(null)
    let input: Record<string, unknown>
    try {
      input = inputFields && inputFields.length > 0 ? buildInputFromForm(inputFields, inputForm) : {}
    } catch (e) {
      setCreateErr(e instanceof Error ? e.message : String(e))
      return
    }
    setCreateBusy(true)
    try {
      const { task_id } = await createTask({ workflow_key: newWorkflowKey, name: nameTrim, input })
      setCreateBusy(false)
      onClose()
      onTaskCreated(task_id)
    } catch (e) {
      setCreateErr(e instanceof Error ? e.message : String(e))
      setCreateBusy(false)
    }
  }

  const onWorkflowChange = (key: string) => {
    setNewWorkflowKey(key)
    applyInputDefaults(workflowOptions.find((w) => w.key === key) ?? null, setInputForm)
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="新建任务">
      <div className="modal">
        <header className="modal-header">
          <h3>新建任务</h3>
          <button type="button" className="btn btn-secondary" onClick={onClose}>关闭</button>
        </header>
        <div className="modal-body">
          {workflowsLoading && <p className="muted small">加载工作流…</p>}
          {workflowsErr && <p className="err">{workflowsErr}</p>}
          <label className="lbl" htmlFor="nt-name">名称（必填）</label>
          <input id="nt-name" type="text" className="input-text" value={newTaskName}
            onChange={(e) => setNewTaskName(e.target.value)} autoComplete="off" />
          <label className="lbl" htmlFor="nt-workflow">Workflow</label>
          <select id="nt-workflow" className="select-field" value={newWorkflowKey}
            disabled={workflowsLoading || workflowOptions.length === 0}
            onChange={(e) => onWorkflowChange(e.target.value)}>
            {!workflowOptions.length && !workflowsLoading && <option value="">暂无工作流</option>}
            {workflowOptions.map((w) => (
              <option key={`${w.key}@${w.revision}`} value={w.key}>{w.key} (rev {w.revision})</option>
            ))}
          </select>
          {inputFields && inputFields.length > 0 && (
            <div className="modal-input-fields">
              <p className="lbl strong">工作流参数 (input)</p>
              {inputFields.map((f) => (
                <div key={f.key} className="field-row">
                  <label className="lbl" htmlFor={`nt-in-${f.key}`}>
                    {f.title}{f.required ? <span className="req-mark"> *</span> : null}
                  </label>
                  {f.type === 'boolean' ? (
                    <label className="check-row">
                      <input id={`nt-in-${f.key}`} type="checkbox"
                        checked={inputForm[f.key] === 'true'}
                        onChange={(e) => setInputForm((prev) => ({ ...prev, [f.key]: e.target.checked ? 'true' : 'false' }))} />
                      <span className="muted small">是 / 否</span>
                    </label>
                  ) : (
                    <input id={`nt-in-${f.key}`}
                      type={f.type === 'number' || f.type === 'integer' ? 'number' : 'text'}
                      className="input-text" value={inputForm[f.key] ?? ''}
                      onChange={(e) => setInputForm((prev) => ({ ...prev, [f.key]: e.target.value }))}
                      autoComplete="off" />
                  )}
                </div>
              ))}
            </div>
          )}
          {createErr && <p className="err">{createErr}</p>}
        </div>
        <footer className="modal-footer">
          <button type="button" className="btn btn-secondary" onClick={onClose}>取消</button>
          <button type="button" className="btn btn-primary"
            disabled={createBusy || workflowsLoading}
            onClick={() => { void onSubmit() }}>
            {createBusy ? '创建中…' : '创建'}
          </button>
        </footer>
      </div>
    </div>
  )
}

function applyInputDefaults(wf: WorkflowInfo | null | undefined, setForm: (v: Record<string, string>) => void) {
  const fields = parseInputSchema(wf?.input_schema ?? null)
  if (!fields) { setForm({}); return }
  const next: Record<string, string> = {}
  for (const f of fields) {
    next[f.key] = f.type === 'boolean' ? 'false' : ''
  }
  setForm(next)
}
