/** JSON Schema helpers for input forms. */

export type InputFieldSpec = {
  key: string
  required: boolean
  type: string
  title: string
}

export function defaultPayloadDraftFromSchema(
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

export function parseInputSchema(schema: unknown): InputFieldSpec[] | null {
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

export function buildInputFromForm(
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
