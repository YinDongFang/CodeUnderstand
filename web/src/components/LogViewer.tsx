import { splitLogLinesIntoRuns, logRunTitle, groupLogLines } from '../utils/log'

interface LogViewerProps {
  logLines: string[]
  logsErr: string | null
}

export default function LogViewer({ logLines, logsErr }: LogViewerProps) {
  const logRuns = splitLogLinesIntoRuns(logLines)

  return (
    <>
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
                className={isLatest ? 'log-run-card log-run-card-latest' : 'log-run-card'}
              >
                <details className="log-run" open={isLatest}>
                  <summary className="log-run-summary">
                    <span className="log-run-summary-left">
                      <span className="log-run-badge">Run {ri + 1}/{logRuns.length}</span>
                      <span className="log-run-title">{runTitle}</span>
                    </span>
                    <span className="muted log-summary-meta">{runLines.length} lines</span>
                  </summary>
                  <div className="log-run-body">
                    {nodeGroups.map((g, i) => (
                      <details key={`${ri}-${g.title}-${i}`} className="log-block" open={isLatest}>
                        <summary className="log-summary">
                          <span className="log-summary-label">{g.title}</span>
                          <span className="muted log-summary-meta">{g.lines.length} lines</span>
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
  )
}
