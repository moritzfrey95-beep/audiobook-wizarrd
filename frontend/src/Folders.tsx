import { useEffect, useState } from 'react'
import { FolderTree, Save } from 'lucide-react'
import { api } from './api'
import { Button } from './ui'
import { t } from './i18n'
import type { CfgView } from './Connections'

interface Preview { ok: boolean; text: string; examples: string[]; presets: Record<string, { series: string; single: string }> }

const input = 'h-10 w-full rounded-lg border border-line bg-bg px-3 font-mono text-sm'
const PRESETS: { key: string; label: string }[] = [
  { key: 'abw', label: t('Serie / Book 1 - Titel') },
  { key: 'author', label: t('Autor / Serie / Book 1 - Titel') },
  { key: 'author_num', label: t('Autor / Serie / 1 - Titel') },
]

/** Ordnerstruktur beim Ablegen: Vorlage wählen oder eigenes Schema, mit Beispielen vom Server. */
export function FolderForm({ cfg, onSaved, saveLabel = t('Speichern') }: { cfg: CfgView; onSaved: (c: CfgView) => void; saveLabel?: string }) {
  const [series, setSeries] = useState(cfg.fields.FOLDER_SERIES?.value ?? '')
  const [single, setSingle] = useState(cfg.fields.FOLDER_SINGLE?.value ?? '')
  const [prev, setPrev] = useState<Preview | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [saved, setSaved] = useState({ series, single })
  const dirty = series !== saved.series || single !== saved.single

  useEffect(() => {
    const id = setTimeout(() => {
      api<Preview>('/config/folders', { method: 'POST', body: JSON.stringify({ series, single }) }).then(setPrev).catch(() => {})
    }, 250)
    return () => clearTimeout(id)
  }, [series, single])

  const active = prev && Object.entries(prev.presets).find(([, p]) => p.series === series && p.single === single)?.[0]
  const choose = (key: string) => { const p = prev?.presets[key]; if (p) { setSeries(p.series); setSingle(p.single) } }
  const save = async () => {
    setBusy(true); setMsg(null)
    try {
      const c = await api<CfgView>('/config', { method: 'PUT', body: JSON.stringify({ values: { FOLDER_SERIES: series, FOLDER_SINGLE: single } }) })
      setMsg({ ok: true, text: t('Gespeichert') }); setSaved({ series, single }); onSaved(c)
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }) } finally { setBusy(false) }
  }

  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <p className="mb-4 text-sm text-muted">{t('So legt ABW neue Bücher in der Bibliothek ab. Gibt es von einer Serie schon Bände, kommen neue in denselben Ordner. Vorhandene Ordner verschiebt ABW nie.')}</p>
      <fieldset className="mb-4">
        <legend className="mb-2 text-sm font-semibold">{t('Vorlage')}</legend>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          {PRESETS.map(p => (
            <label key={p.key} className={`flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm ${active === p.key ? 'border-accent bg-accent/10' : 'border-line hover:bg-surface-2'}`}>
              <input type="radio" name="folder-preset" checked={active === p.key} onChange={() => choose(p.key)} className="h-4 w-4" />
              {p.label}
            </label>
          ))}
        </div>
        {prev && !active && <p className="mt-2 text-xs text-muted">{t('Eigenes Schema')}</p>}
      </fieldset>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div>
          <label htmlFor="cfg-FOLDER_SERIES" className="mb-1 block text-sm font-semibold">{t('Bände einer Serie')}</label>
          <input id="cfg-FOLDER_SERIES" className={input} value={series} onChange={e => setSeries(e.target.value)} spellCheck={false} autoComplete="off" />
        </div>
        <div>
          <label htmlFor="cfg-FOLDER_SINGLE" className="mb-1 block text-sm font-semibold">{t('Einzelbücher')}</label>
          <input id="cfg-FOLDER_SINGLE" className={input} value={single} onChange={e => setSingle(e.target.value)} spellCheck={false} autoComplete="off" />
        </div>
      </div>
      <p className="mt-2 text-xs text-muted">
        {t('Platzhalter:')} <code>{'{author}'}</code> <code>{'{series}'}</code> <code>{'{seq}'}</code> <code>{'{title}'}</code> <code>{'{narrator}'}</code> <code>{'{year}'}</code>.{' '}
        {t('«/» trennt Ordner. Text in < > fällt weg, wenn ein Wert darin fehlt, etwa die Bandnummer.')}
      </p>
      <div className="mt-4 rounded-lg bg-surface-2 p-3 text-sm" aria-live="polite">
        <p className="mb-1 flex items-center gap-1.5 font-semibold"><FolderTree size={15} aria-hidden />{t('Beispiele')}</p>
        {prev && !prev.ok ? <p className="text-bad">{prev.text}</p>
          : <ul className="space-y-0.5 font-mono text-xs">{(prev?.examples ?? []).map(x => <li key={x} className="break-all">{x}/</li>)}</ul>}
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button variant="primary" onClick={save} disabled={busy || !prev?.ok}><Save size={15} aria-hidden />{saveLabel}</Button>
        {dirty ? <span role="status" className="text-sm text-warn">{t('Noch nicht gespeichert')}</span>
          : msg && <span role="status" className={`text-sm ${msg.ok ? 'text-muted' : 'text-bad'}`}>{msg.text}</span>}
      </div>
    </div>
  )
}
