import type { TaskDetail as TaskDetailType } from '../api'
import { displayTaskName, hasDictContent, chipClass, TASK_TERMINAL } from '../utils/display'
import { formatTaskDisplayTime, formatWallSeconds } from '../utils/format'
import KvBlock from './KvBlock'
import NodeStrip from './NodeStrip'
import InterruptPanel from './InterruptPanel'
import LogViewer from './LogViewer'

interface TaskDetailProps {
  detail: TaskDetailType
  detailErr: string | null
  nowTick: number
  nodeActionErr: string | null
  rerunBusyNodeId: string | null
  resolveDraft: string
  resolveErr: string | null
  resolveBusy: boolean
  logLines: string[]
  logsErr: string | null
  onRerun: (nodeId: string) => void
  onResolve: () => void
  onResolveDraftChange: (value: string) => void
}

export default function TaskDetail({
  detail, detailErr, nowTick, nodeActionErr, rerunBusyNodeId,
  resolveDraft, resolveErr, resolveBusy, logLines, logsErr,
  onRerun, onResolve, onResolveDraftChange,
}: TaskDetailProps) {
  return (
    <>
      {detailErr && <p className="err">{detailErr}</p>}
      <h2 className="task-detail-title">{displayTaskName(detail)}</h2>
      <dl className="meta">
        <dt>Id</dt>
        <dd className="mono">{detail.id}</dd>
        <dt>Workflow</dt>
        <dd>{detail.workflow_key}</dd>
        <dt>Status</dt>
        <dd><span className={chipClass(detail.status)}>{detail.status}</span></dd>
        <dt>Created at</dt>
        <dd>{formatTaskDisplayTime(detail.created_at)}</dd>
        <dt>Completed at</dt>
        <dd>{TASK_TERMINAL.has(detail.status) ? formatTaskDisplayTime(detail.updated_at) : '—'}</dd>
        <dt>Execution count</dt>
        <dd>{detail.execution_count ?? 0}</dd>
        <dt>Active duration</dt>
        <dd title="Wall time excluding waiting_human (interrupt)">
          {formatWallSeconds(detail.active_duration_seconds ?? 0)} (excl. interrupt)
        </dd>
      </dl>

      {hasDictContent(detail.input) && <KvBlock title="Input" data={detail.input} />}
      {hasDictContent(detail.context) && <KvBlock title="Context" data={detail.context} />}

      <NodeStrip
        nodes={detail.nodes}
        nowTick={nowTick}
        taskStatus={detail.status}
        rerunBusyNodeId={rerunBusyNodeId}
        nodeActionErr={nodeActionErr}
        onRerun={onRerun}
      />

      {detail.interrupt && (
        <InterruptPanel
          interrupt={detail.interrupt}
          resolveDraft={resolveDraft}
          resolveErr={resolveErr}
          resolveBusy={resolveBusy}
          onResolveDraftChange={onResolveDraftChange}
          onResolve={onResolve}
        />
      )}

      <LogViewer logLines={logLines} logsErr={logsErr} />
    </>
  )
}
