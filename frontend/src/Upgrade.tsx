import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Loader2, Search } from 'lucide-react'
import { api, fmtDateTime, useData } from './api'
import { Button, Chip } from './ui'
import { locale, t } from './i18n'

interface Item {
  id: number; title: string; lang: string; sequence: string | null; series: string | null; series_id: number | null
  kbps: number; reason: 'weak' | 'short'; reason_text: string; last_search: string | null; next_search: string | null; last_result: string | null; text: string | null
}
interface Overview { enabled: boolean; kbps: number; weak: number; short: number; items: Item[] }

const resultText = (r: string | null) => r === 'started' ? t('Bessere Fassung lädt') : r === 'none' ? t('Nichts Besseres gefunden') : r === 'error' ? t('Fehler bei der Suche') : r === 'skipped' ? t('Download läuft schon') : t('Noch nicht gesucht')

/** Bücher im Bestand mit schwacher Tonqualität oder zu kurz: Stand der Suche nach einer besseren Fassung. */
export function UpgradePanel() {
  const { data, reload } = useData<Overview>('/upgrade', 30000)
  const [show, setShow] = useState<'weak' | 'short'>('weak')
  const [busy, setBusy] = useState<number | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  if (!data) return <div className="skeleton h-32 rounded-xl" />
  const items = data.items.filter(x => x.reason === show)
  const search = async (id: number) => {
    setBusy(id); setMsg(null)
    try { const r = await api<{ text: string }>(`/upgrade/${id}`, { method: 'POST' }); setMsg(r.text); reload() }
    catch (e) { setMsg((e as Error).message) } finally { setBusy(null) }
  }
  const setKbps = async (v: number) => {
    try { await api('/upgrade/settings', { method: 'POST', body: JSON.stringify({ kbps: v }) }); reload() } catch (e) { setMsg((e as Error).message) }
  }
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Chip active={show === 'weak'} onClick={() => setShow('weak')} count={data.weak}>{t('Schwache Tonqualität')}</Chip>
        <Chip active={show === 'short'} onClick={() => setShow('short')} count={data.short}>{t('Kürzer als bei Audible')}</Chip>
        <label className="ml-auto flex items-center gap-2 text-sm text-muted">
          {t('Schwelle')}
          <select className="min-h-9 rounded-lg border border-line bg-surface-2 px-2" value={data.kbps} onChange={e => setKbps(Number(e.target.value))}>
            {[24, 32, 40, 48, 64].map(v => <option key={v} value={v}>{t('{{v0}} kbit/s', { v0: v })}</option>)}
          </select>
        </label>
      </div>
      <p className="mt-2 text-sm text-muted">
        {show === 'weak'
          ? t('Die Automatik sucht für diese Bücher eine Fassung mit deutlich mehr Daten je Minute. Ersetzt wird erst nach der Ankunftsprüfung und nur, wenn der neue Band eindeutig besser ist.')
          : t('Oft eine andere Aufnahme mit anderem Sprecher. Die Automatik sucht hier nicht selbst; «Suchen» lädt nur einen sicheren Treffer mit besserer Qualität.')}
      </p>
      {msg && <p role="status" className="mt-2 text-sm">{msg}</p>}
      {items.length === 0
        ? <p className="mt-3 text-sm text-muted">{t('Keine Bücher in dieser Liste.')}</p>
        : (
          <ul className="mt-3 divide-y divide-line text-sm">
            {items.slice(0, 100).map(x => (
              <li key={x.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                <span className="min-w-0 flex-1">
                  {x.series_id
                    ? <Link to={`/serie/${x.series_id}`} className="font-medium hover:underline">{x.title}</Link>
                    : <span className="font-medium">{x.title}</span>}
                  <span className="block text-muted">{x.reason_text} · {x.last_search ? t('{{v0}}, zuletzt {{v1}}', { v0: resultText(x.last_result), v1: fmtDateTime(x.last_search) }) : resultText(null)}</span>
                </span>
                <span className="tabular w-20 text-right text-muted">{t('{{v0}} kbit/s', { v0: x.kbps.toLocaleString(locale()) })}</span>
                <Button disabled={busy !== null} onClick={() => search(x.id)}>
                  {busy === x.id ? <Loader2 size={14} className="animate-spin" aria-hidden /> : <Search size={14} aria-hidden />}{t('Suchen')}
                </Button>
              </li>
            ))}
          </ul>
        )}
    </div>
  )
}
