import { createContext, useContext, useState } from 'react'
import { BookmarkCheck, BookmarkPlus, Loader2 } from 'lucide-react'
import { api } from './api'
import { t } from './i18n'

/** Schlüssel der Einträge auf «To Read» («e12» = Fassung 12, «b40» = Buch 40) und ein Auffrischen danach. */
export const ToReadKeys = createContext<{ keys: Set<string>; reload: () => void }>({ keys: new Set(), reload: () => {} })

/** Knopf «Auf To Read»: setzt eine ganze Serie (Fassung) oder ein Einzelbuch auf die Liste oder nimmt es wieder herunter. */
export function ToReadButton({ kind, id, title }: { kind: 'edition' | 'book'; id: number; title: string }) {
  const { keys, reload } = useContext(ToReadKeys)
  const key = `${kind[0]}${id}`
  const [over, setOver] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const on = over ?? keys.has(key)
  const toggle = async (ev: React.MouseEvent) => {
    ev.preventDefault(); ev.stopPropagation()
    setBusy(true); setErr(null)
    try {
      if (on) await api(`/toread/${kind}/${id}`, { method: 'DELETE' })
      else await api('/toread', { method: 'POST', body: JSON.stringify({ kind, id }) })
      setOver(!on); reload()
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  return (
    <>
      <button type="button" onClick={toggle} disabled={busy} aria-pressed={on}
        title={on ? t('«{{v0}}» steht auf To Read. Klicken zum Entfernen.', { v0: title }) : kind === 'edition' ? t('Setzt die Serie auf To Read; die Liste zeigt immer den nächsten Band, den du noch nicht gehört hast') : t('Setzt das Buch auf To Read')}
        className={`inline-flex min-h-9 items-center gap-1.5 rounded-lg border px-3 text-xs font-semibold transition-colors disabled:opacity-50 ${
          on ? 'border-accent text-accent' : 'border-line hover:border-accent hover:text-accent'}`}>
        {on ? <BookmarkCheck size={14} aria-hidden /> : <BookmarkPlus size={14} aria-hidden />}{on ? t('Auf To Read') : t('Zu To Read')}
      </button>
      {err && <span role="alert" className="text-xs" style={{ color: 'var(--bad)' }}>{err}</span>}
    </>
  )
}

/** Audible-Nummern auf «To Read» (Serien und ihre Bände), für die Knöpfe unter Entdecken. */
/** asins: aktueller Stand für die Knöpfe; hide: Stand beim Laden der Seite für den Filter «auf To Read ausblenden»
 *  (so verschwindet eine Karte nicht schon beim Klick). */
export const ToReadAsins = createContext<{ asins: Set<string>; hide: Set<string>; reload: () => void }>({ asins: new Set(), hide: new Set(), reload: () => {} })

export interface DiscoverItem { asin: string; lang?: string; title?: string | null; author?: string | null; series?: string | null; cover_url?: string | null; kind?: 'book' | 'series' }

/** «Zu To Read» unter Entdecken: setzt die Serie (oder das Einzelbuch) auf die Liste; fehlt der Titel in ABW, wird er
 *  zuerst wie mit «Hinzufügen» in die Bibliothek aufgenommen. */
export function DiscoverToReadButton({ item }: { item: DiscoverItem }) {
  const { asins, reload } = useContext(ToReadAsins)
  const [over, setOver] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const on = over ?? asins.has(item.asin)
  const add = async (ev: React.MouseEvent) => {
    ev.preventDefault(); ev.stopPropagation()
    if (on) return
    setBusy(true); setNote(null)
    try {
      const r = await api<{ ok: boolean; pending?: boolean; message?: string; library?: boolean }>('/toread/discover', { method: 'POST', body: JSON.stringify(item) })
      setOver(true); reload()
      setNote(r.pending ? (r.message ?? t('Vorgemerkt.')) : r.library ? t('In die Bibliothek aufgenommen und auf To Read gesetzt.') : null)
    } catch (e) { setNote((e as Error).message) } finally { setBusy(false) }
  }
  return (
    <>
      <button type="button" onClick={add} disabled={busy || on} aria-pressed={on}
        title={on ? t('Steht auf To Read') : item.kind === 'series' ? t('Setzt die Serie auf To Read; fehlt sie in ABW, wird sie auch in die Bibliothek aufgenommen und gesucht') : t('Setzt die Serie dieses Buchs (oder das Einzelbuch) auf To Read; fehlt es in ABW, wird es auch in die Bibliothek aufgenommen und gesucht')}
        className={`flex min-h-9 w-full items-center justify-center gap-1.5 rounded-lg border text-xs font-semibold transition-colors disabled:cursor-default ${
          on ? 'border-accent text-accent' : 'border-line hover:border-accent hover:text-accent disabled:opacity-50'}`}>
        {busy ? <Loader2 size={14} aria-hidden className="animate-spin" /> : on ? <BookmarkCheck size={14} aria-hidden /> : <BookmarkPlus size={14} aria-hidden />}
        {busy ? t('Setzt…') : on ? t('Auf To Read') : t('Zu To Read')}
      </button>
      {note && <p role="status" className="mt-1 text-xs text-muted">{note}</p>}
    </>
  )
}
