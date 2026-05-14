/** Browser calls `/api/*`; Vite proxies to uvicorn and strips the `/api` prefix. */
const BASE = '/api'

async function parseJson<T>(res: Response): Promise<T> {
  const text = await res.text()
  let body: unknown
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    throw new Error(`Invalid JSON (${res.status}): ${text.slice(0, 200)}`)
  }
  if (!res.ok) {
    const msg =
      typeof body === 'object' && body !== null && 'detail' in body
        ? JSON.stringify((body as { detail: unknown }).detail)
        : text.slice(0, 300)
    throw new Error(`HTTP ${res.status}: ${msg}`)
  }
  return body as T
}

export type TaskSummary = {
  id: string
  status: string
  workflow_key: string
  created_at: string
}

export type TaskNode = {
  node_id: string
  ordinal: number
  status: string
  started_at: string | null
  finished_at: string | null
  zip_path: string | null
  error?: Record<string, unknown>
}

export type InterruptInfo = {
  seq: number
  node_id: string
  expected_schema: Record<string, unknown> | null
  request_extras: Record<string, unknown> | null
  checkpoint: Record<string, unknown> | null
}

export type TaskDetail = {
  id: string
  workflow_key: string
  workflow_revision: string
  status: string
  input: Record<string, unknown>
  created_at: string
  updated_at: string
  worker_generation: number
  nodes: TaskNode[]
  interrupt?: InterruptInfo
}

export type LogsResponse = {
  lines: string[]
  next_cursor: number
}

export function fetchTasks(): Promise<TaskSummary[]> {
  return fetch(`${BASE}/tasks`).then((r) => parseJson<TaskSummary[]>(r))
}

export function fetchTask(taskId: string): Promise<TaskDetail> {
  return fetch(`${BASE}/tasks/${encodeURIComponent(taskId)}`).then((r) =>
    parseJson<TaskDetail>(r),
  )
}

export function fetchLogs(taskId: string, cursor: number): Promise<LogsResponse> {
  const q = new URLSearchParams({ cursor: String(cursor) })
  return fetch(`${BASE}/tasks/${encodeURIComponent(taskId)}/logs?${q}`).then((r) =>
    parseJson<LogsResponse>(r),
  )
}

export function resolveInterrupt(
  taskId: string,
  body: { interrupt_seq?: number; payload: Record<string, unknown> },
): Promise<{ status: string }> {
  return fetch(`${BASE}/tasks/${encodeURIComponent(taskId)}/interrupt/resolve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then((r) => parseJson<{ status: string }>(r))
}
