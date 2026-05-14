import type { InterruptInfo } from '../api'

interface InterruptPanelProps {
  interrupt: InterruptInfo
  resolveDraft: string
  resolveErr: string | null
  resolveBusy: boolean
  onResolveDraftChange: (value: string) => void
  onResolve: () => void
}

export default function InterruptPanel({ interrupt, resolveDraft, resolveErr, resolveBusy, onResolveDraftChange, onResolve }: InterruptPanelProps) {
  return (
    <section className="interrupt">
      <h3>Interrupt</h3>
      <pre className="json">{JSON.stringify(interrupt, null, 2)}</pre>
      <label className="lbl" htmlFor="payload-json">Resolve payload (JSON object)</label>
      <textarea
        id="payload-json"
        className="textarea"
        rows={6}
        spellCheck={false}
        value={resolveDraft}
        onChange={(e) => onResolveDraftChange(e.target.value)}
      />
      {resolveErr && <p className="err">{resolveErr}</p>}
      <button
        type="button"
        className="btn btn-primary"
        disabled={resolveBusy}
        onClick={() => { void onResolve() }}
      >
        {resolveBusy ? 'Posting…' : 'POST /interrupt/resolve'}
      </button>
      <p className="muted small">
        Body: <code className="mono">{`{ interrupt_seq, payload }`}</code>
      </p>
    </section>
  )
}
