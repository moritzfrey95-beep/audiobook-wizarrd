import { useState } from 'react'
import { Check, Plus } from 'lucide-react'
import { api } from './api'
import { ask } from './ui'
import { t } from './i18n'

export interface WatchItem { asin: string; lang?: string; title?: string | null; author?: string | null; series?: string | null; cover_url?: string | null; kind?: 'book' | 'series' }

/** Hinzufügen: nimmt den Titel oder die Serie als fehlend in die Bibliothek auf; die Suche läuft dann mit Vorrang. */
export function WatchButton({ item, watched, onChange }: { item: WatchItem; watched: boolean; onChange?: () => void }) {
  const [on, setOn] = useState(watched)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const toggle = async (ev: React.MouseEvent) => {
    ev.preventDefault(); ev.stopPropagation()
    if (on && !await ask(t('«{{v0}}» wieder entfernen? Was noch nicht geladen ist, wird nicht mehr gesucht. Bücher im Bestand bleiben.', { v0: item.title ?? item.asin }), t('Entfernen'))) return
    setBusy(true); setNote(null)
    try {
      if (on) await api(`/watchlist/${item.asin}`, { method: 'DELETE' })
      else {
        const r = await api<{ ok: boolean; message?: string }>('/watchlist', { method: 'POST', body: JSON.stringify(item) })
        if (!r.ok && r.message) setNote(r.message)
      }
      setOn(!on); onChange?.()
    } catch (e) { setNote((e as Error).message) } finally { setBusy(false) }
  }
  return (
    <>
      <button type="button" onClick={toggle} disabled={busy} aria-pressed={on}
        title={on ? t('In der Bibliothek, wird gesucht. Klicken zum Entfernen.') : item.kind === 'series' ? t('Nimmt die Serie in die Bibliothek auf und sucht alle Bände') : t('Nimmt das Buch in die Bibliothek auf und sucht es; gehört es zu einer Serie, kommt die ganze Serie mit')}
        className={`flex min-h-9 w-full items-center justify-center gap-1.5 rounded-lg border text-xs font-semibold transition-colors disabled:opacity-50 ${
          on ? 'border-accent text-accent' : 'border-line hover:border-accent hover:text-accent'}`}>
        {on ? <Check size={14} aria-hidden /> : <Plus size={14} aria-hidden />}{busy ? (on ? t('Entfernt…') : t('Fügt hinzu…')) : on ? t('Hinzugefügt') : t('Hinzufügen')}
      </button>
      {note && <p role="status" className="mt-1 text-xs text-muted">{note}</p>}
    </>
  )
}
