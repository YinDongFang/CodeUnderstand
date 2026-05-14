/** Log line parsing: split by worker runs, group by node marker. */

export const LOG_RUN_BEGIN = 'WF_ENGINE_RUN_BEGIN'
const LOG_NODE_MARKER = /\[\d+:[^\]]+\]/

export function splitLogLinesIntoRuns(lines: string[]): string[][] {
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

export function logRunTitle(runLines: string[], runIndex: number, totalRuns: number): string {
  const first = runLines[0] ?? ''
  const m = first.match(/WF_ENGINE_RUN_BEGIN round=(\d+)/)
  if (m) return `Round ${m[1]}`
  if (totalRuns === 1) return 'Log'
  return runIndex === 0 ? 'Legacy log (no run marker)' : `Section ${runIndex + 1}`
}

export function groupLogLines(lines: string[]): Array<{ title: string; lines: string[] }> {
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
