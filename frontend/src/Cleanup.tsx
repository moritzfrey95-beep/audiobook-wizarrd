import { useState } from 'react'
import { Archive, Loader2, Trash2 } from 'lucide-react'
import { api, fmtDateTime, useData } from './api'
import { ask, Button } from './ui'
import { locale, t } from './i18n'

interface Cand { source: string; marker: string; bytes: number; files: number; title: string; reason: string; finished: string | null }
interface Trashed { day: string; marker: string; bytes: number; files: number }
interface Overview { candidates: Cand[]; trash: Trashed[]; keep_days: number }

const mb = (b: number) => b > 1e9 ? t('{{v0}} GB', { v0: (b / 1e9).toLocaleString(locale(), { maximumFractionDigits: 1 }) }) : t('{{v0}} MB', { v0: Math.round(b / 1e6).toLocaleString(locale()) })

/** Download-Reste: erst ansehen, dann in den Papierkorb verschieben. Endgültig gelöscht wird getrennt. */
export function CleanupPanel() {
  const { data, reload } = useData<Overview>('/cleanup', 30000)
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  if (!data) return <div className="skeleton h-32 rounded-xl" />
  const total = data.candidates.reduce((n, c) => n + c.bytes, 0)
  const trashTotal = data.trash.reduce((n, c) => n + c.bytes, 0)
  const limit = new Date(Date.now() - data.keep_days * 86400000).toISOString().slice(0, 10)
  const old = data.trash.filter(tx => tx.day <= limit)
  const act = async (path: string, key: string, done: (r: { moved?: number; removed?: number; bytes: number }) => string) => {
    setBusy(key); setMsg(null)
    try { setMsg(done(await api(path, { method: 'POST', body: '{}' }))); reload() }
    catch (e) { setMsg(t('Das hat nicht geklappt. {{v0}}', { v0: (e as Error).message })) } finally { setBusy(null) }
  }
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      {data.candidates.length === 0
        ? <p className="text-sm text-muted">{t('Keine Download-Reste vorhanden.')}</p>
        : (
          <>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-sm"><span className="font-semibold">{data.candidates.length} {t('Ordner,')}{' '}{mb(total)}</span> <span className="text-muted">{t('werden nicht mehr gebraucht.')}</span></p>
              <Button disabled={busy !== null} onClick={() => act('/cleanup/run', 'run', r => `${r.moved} Ordner in den Papierkorb verschoben (${mb(r.bytes)}).`)}>
                {busy === 'run' ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <Archive size={15} aria-hidden />}{t('In den Papierkorb verschieben')}
              </Button>
            </div>
            <ul className="mt-3 divide-y divide-line text-sm">
              {data.candidates.map(c => (
                <li key={c.marker} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-2">
                  <span className="min-w-0 flex-1 truncate font-medium">{c.title}</span>
                  <span className="text-muted">{c.source} · {c.reason}{c.finished ? t(', {{v0}}', { v0: fmtDateTime(c.finished) }) : ''}</span>
                  <span className="tabular w-20 text-right text-muted">{mb(c.bytes)}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
        <p className="text-sm text-muted">
          {t('Papierkorb:')}{' '}{data.trash.length} {t('Ordner,')}{' '}{mb(trashTotal)}{t('. Davon')}{' '}{old.length} {t('älter als')}{' '}{data.keep_days} {t('Tage.')}
        </p>
        <Button disabled={busy !== null || old.length === 0}
          onClick={async () => (await ask(`${old.length} Ordner endgültig löschen? Das lässt sich nicht rückgängig machen.`, 'Endgültig löschen')) && act('/cleanup/purge', 'purge', r => `${r.removed} Ordner endgültig gelöscht (${mb(r.bytes)}).`)}
          title={old.length ? undefined : t('Erst nach {{v0}} Tagen im Papierkorb möglich', { v0: data.keep_days })}>
          {busy === 'purge' ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <Trash2 size={15} aria-hidden />}{t('Ältere endgültig löschen')}
        </Button>
      </div>
      {msg && <p className="mt-3 text-sm" aria-live="polite">{msg}</p>}
    </div>
  )
}
