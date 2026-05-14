import type { TaskSummary } from '../api'
import { displayTaskName, chipClass } from '../utils/display'
import { formatWallSeconds } from '../utils/format'

interface TaskListProps {
  tasks: TaskSummary[]
  tasksErr: string | null
  selectedId: string | null
  rightPanelTab: string
  onSelectTask: (id: string) => void
  onOpenSettings: () => void
  onOpenCreate: () => void
}

export default function TaskList({ tasks, tasksErr, selectedId, rightPanelTab, onSelectTask, onOpenSettings, onOpenCreate }: TaskListProps) {
  return (
    <aside className="pane left">
      <div className="left-head">
        <h2 className="left-title">任务</h2>
        <div className="left-head-actions">
          <button type="button" className="btn btn-secondary" onClick={onOpenSettings}>系统设置</button>
          <button type="button" className="btn btn-primary" onClick={onOpenCreate}>新建任务</button>
        </div>
      </div>
      {tasksErr && <p className="err">{tasksErr}</p>}
      <ul className="task-list">
        {tasks.map((t) => (
          <li key={t.id}>
            <button
              type="button"
              className={t.id === selectedId && rightPanelTab === 'detail' ? 'task-row active' : 'task-row'}
              onClick={() => onSelectTask(t.id)}
              title={`${t.workflow_key} · ${t.id} · Run ${t.execution_count ?? 0} · ${formatWallSeconds(t.active_duration_seconds ?? 0)} active (excl. interrupt)`}
            >
              <span className="task-row-main">
                <span className="task-row-name">{displayTaskName(t)}</span>
                <span className={chipClass(t.status)}>{t.status}</span>
              </span>
              <span className="task-row-meta muted small">
                Run {t.execution_count ?? 0} · {formatWallSeconds(t.active_duration_seconds ?? 0)} active
              </span>
            </button>
          </li>
        ))}
      </ul>
      {!tasks.length && !tasksErr && <p className="muted">暂无任务</p>}
    </aside>
  )
}
