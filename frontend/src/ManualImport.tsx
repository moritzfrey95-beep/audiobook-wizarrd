import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { EyeOff, FolderInput, Loader2, RefreshCw } from 'lucide-react'
import { api, fmtDateTime, useData } from './api'
import { Button, Empty, LangTag } from './ui'
import { locale, t } from './i18n'

interface Sug { book_id: number; title: string; series: string | null; series_id: number | null; sequence: string | null; lang: string; owned: boolean; points: number }
interface Job { running: boolean; text: string; error?: boolean; download_id: number | null }
interface Item { path: string; root: string; name: string; rel: string; bytes: number; files: number; modified: string | null; suggestions: Sug[]; job: Job | null }
interface Overview { roots: { name: string; path: string }[]; items: Item[] }

const size = (b: number) => b > 1e9 ? t('{{v0}} GB', { v0: (b / 1e9).toLocaleString(locale(), { maximumFractionDigits: 1 }) }) : t('{{v0}} MB', { v0: Math.round(b / 1e6).toLocaleString(locale()) })

function SugLabel({ s }: { s: Sug }) {
  return (
    <span className="min-w-0">
      <LangTag lang={s.lang} />{' '}
      <span className="font-medium">{s.title}</span>
      {s.series && <span className="text-muted">{' · '}{s.series}{s.sequence ? t(', Band {{v0}}', { v0: s.sequence }) : ''}</span>}
      {s.owned && <span className="text-muted">{' · '}{t('im Bestand, wird verglichen')}</span>}
    </span>
  )
}

function FolderRow({ it, onChange }: { it: Item; onChange: () => void }) {
  const [q, setQ] = useState('')
  const [found, setFound] = useState<Sug[] | null>(null)
  const [choice, setChoice] = useState<Sug | null>(it.suggestions[0] ?? null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  useEffect(() => {
    if (q.trim().length < 2) return
    const h = setTimeout(() => { api<Sug[]>(`/manual/books?q=${encodeURIComponent(q)}`).then(setFound).catch(() => setFound([])) }, 300)
    return () => clearTimeout(h)
  }, [q])
  const start = async () => {
    if (!choice) return
    setBusy(true); setMsg(null)
    try { const j = await api<Job>('/manual', { method: 'POST', body: JSON.stringify({ path: it.path, book_id: choice.book_id }) }); setMsg(j.text); onChange() }
    catch (e) { setMsg((e as Error).message) } finally { setBusy(false) }
  }
  const hide = async () => { await api('/manual/hide', { method: 'POST', body: JSON.stringify({ path: it.path }) }); onChange() }
  const options = q.trim().length >= 2 ? found ?? [] : it.suggestions
  return (
    <li className="rounded-xl border border-line bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="break-words font-semibold">{it.name}</p>
          <p className="text-sm text-muted">{it.root} · {it.rel} · {t('{{n}} Audiodateien', { n: it.files })} · {size(it.bytes)}{it.modified ? ` · ${fmtDateTime(it.modified)}` : ''}</p>
        </div>
        <Button onClick={hide} title={t('Diesen Ordner nicht mehr anzeigen')}><EyeOff size={14} aria-hidden />{t('Ausblenden')}</Button>
      </div>
      {it.job
        ? <p role="status" className="mt-3 text-sm">{it.job.running && <Loader2 size={14} className="mr-1 inline animate-spin" aria-hidden />}{it.job.text}
            {it.job.download_id && <> {' '}<Link to="/pruefen?tab=active" className="font-medium text-accent hover:underline">{t('Zum Download')}</Link></>}</p>
        : (
          <div className="mt-3 flex flex-col gap-2">
            <input type="search" value={q} onChange={e => setQ(e.target.value)} placeholder={t('Anderes Buch suchen (Titel, Serie, Autor)')}
              className="min-h-10 w-full rounded-lg border border-line bg-surface-2 px-3 text-sm" aria-label={t('Buch suchen')} />
            {options.length === 0
              ? <p className="text-sm text-muted">{t('Kein passendes Buch in der Bibliothek. Füge es zuerst unter Entdecken hinzu.')}</p>
              : (
                <ul className="flex flex-col gap-1 text-sm">
                  {options.map(s => (
                    <li key={s.book_id}>
                      <label className="flex min-h-9 cursor-pointer items-center gap-2">
                        <input type="radio" name={`m-${it.path}`} checked={choice?.book_id === s.book_id} onChange={() => setChoice(s)} />
                        <SugLabel s={s} />
                      </label>
                    </li>
                  ))}
                </ul>
              )}
            <div className="flex flex-wrap items-center gap-3">
              <Button variant="primary" disabled={!choice || busy} onClick={start}>
                {busy ? <Loader2 size={14} className="animate-spin" aria-hidden /> : <FolderInput size={14} aria-hidden />}{t('Übernehmen und prüfen')}
              </Button>
              {msg && <span role="status" className="text-sm">{msg}</span>}
            </div>
          </div>
        )}
    </li>
  )
}

/** Hörbuch-Ordner von Hand einem Buch zuordnen: Kopie, dann normale Ankunftsprüfung und Ablage. */
export function ManualImport() {
  const [refresh, setRefresh] = useState(0)
  const { data, error, reload } = useData<Overview>(`/manual${refresh ? '?refresh=true' : ''}`, 10000)
  if (error && !data) return <p className="text-sm">{error}</p>
  if (!data) return <div className="skeleton h-64 rounded-2xl" />
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted">
          {t('Ordner aus {{v0}}, die zu keinem ABW-Download gehören. «Übernehmen» kopiert den Ordner, danach prüft und legt ABW ihn ab wie jeden Download. Der Ordner selbst bleibt unverändert.', { v0: data.roots.map(r => r.name).join(', ') })}
        </p>
        <Button onClick={() => { setRefresh(n => n + 1); reload() }}><RefreshCw size={14} aria-hidden />{t('Neu einlesen')}</Button>
      </div>
      {data.items.length === 0
        ? <Empty title={t('Keine Ordner gefunden')} text={t('Lege Hörbuch-Ordner in den Eingangsordner (ABW_IMPORT_ROOT, in Docker IMPORT_HOST) oder in ein Download-Ziel.')} />
        : <ul className="grid grid-cols-1 gap-3 xl:grid-cols-2">{data.items.map(it => <FolderRow key={it.path} it={it} onChange={reload} />)}</ul>}
    </div>
  )
}
