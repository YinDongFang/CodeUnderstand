import { useEffect, useRef, useState } from 'react'
import { fetchSettings, saveSettings } from '../api'

export default function SettingsPanel() {
  const opsCookieRef = useRef('')
  const opsAuthRef = useRef('')
  const [tasksRootDraft, setTasksRootDraft] = useState('')
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [saveErr, setSaveErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    setBusy(true)
    fetchSettings()
      .then((s) => {
        if (cancelled) return
        setTasksRootDraft(s.root)
        opsCookieRef.current = s.cookie
        opsAuthRef.current = s.authorization
      })
      .catch((e: Error) => {
        if (!cancelled) setLoadErr(e.message)
      })
      .finally(() => {
        if (!cancelled) setBusy(false)
      })
    return () => { cancelled = true }
  }, [])

  const onSave = async () => {
    setSaveErr(null)
    setBusy(true)
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
      setSaveErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="settings-in-pane" aria-label="系统设置">
      {loadErr && <p className="err">{loadErr}</p>}
      <label className="lbl mono" htmlFor="sys-root">root</label>
      <textarea
        id="sys-root"
        className="textarea mono"
        rows={2}
        spellCheck={false}
        autoComplete="off"
        value={tasksRootDraft}
        onChange={(e) => setTasksRootDraft(e.target.value)}
        disabled={busy}
      />
      {saveErr && <p className="err">{saveErr}</p>}
      <div className="settings-save-row">
        <button type="button" className="btn btn-primary" disabled={busy} onClick={() => { void onSave() }}>
          {busy ? '保存中…' : '保存'}
        </button>
      </div>
    </section>
  )
}
