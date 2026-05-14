import { Fragment } from 'react'
import type { TaskNode } from '../api'
import { chipClass, nodeTiming, sortedNodes } from '../utils/display'

interface NodeStripProps {
  nodes: TaskNode[]
  nowTick: number
  taskStatus: string
  rerunBusyNodeId: string | null
  nodeActionErr: string | null
  onRerun: (nodeId: string) => void
}

export default function NodeStrip({ nodes, nowTick, taskStatus, rerunBusyNodeId, nodeActionErr, onRerun }: NodeStripProps) {
  return (
    <>
      <h3 className="section-heading">Nodes</h3>
      {nodeActionErr && <p className="err">{nodeActionErr}</p>}
      <div className="node-strip" role="list" aria-label="Workflow nodes in execution order, left to right">
        {sortedNodes(nodes).map((n, idx) => {
          const timing = nodeTiming(n, nowTick)
          const errMsg =
            n.error && typeof n.error.message === 'string'
              ? n.error.message
              : n.error
                ? JSON.stringify(n.error)
                : null
          return (
            <Fragment key={`${n.ordinal}-${n.node_id}`}>
              {idx > 0 && <span className="node-sep" aria-hidden>›</span>}
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
                <button
                  type="button"
                  className="btn btn-secondary node-rerun-btn"
                  disabled={taskStatus === 'waiting_human' || rerunBusyNodeId === n.node_id}
                  title={taskStatus === 'waiting_human' ? 'Cannot rerun while waiting for human input' : 'Rerun from this node (POST /tasks/…/rerun)'}
                  onClick={() => { void onRerun(n.node_id) }}
                >
                  {rerunBusyNodeId === n.node_id ? 'Submitting…' : 'Rerun from here'}
                </button>
              </div>
            </Fragment>
          )
        })}
      </div>
    </>
  )
}
