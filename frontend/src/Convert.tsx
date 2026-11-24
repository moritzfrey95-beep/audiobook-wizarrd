import { useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, CheckCircle2, Loader2, SkipForward, Trash2 } from 'lucide-react'
import { api, fmtDateTime, useData } from './api'
import { ask, Button, LangTag } from './ui'
import { locale, t } from './i18n'

interface Job { id: number; state: string; message: string | null; started: string; title: string; lang: string; series: string | null; series_id: number | null; src_bytes: number | null; out_bytes: number | null }
interface Overview {
  pending: number; pending_bytes: number; counts: Record<string, number>; jobs: Job[]; auto: boolean; keep_days: number
  running: { title: string; pct: number } | null; trash: { day: string; files: number; bytes: number }[]
  chapters: { pending: number; counts: Record<string, number>; recent: { book_id: number; state: string; message: string; title: string; at: string }[] }
}
const gb = (b: number) => b > 1e9 ? t('{{v0}} GB', { v0: (b / 1e9).toLocaleString(locale(), { maximumFractionDigits: 1 }) }) : t('{{v0}} MB', { v0: Math.round(b / 1e6).toLocaleString(locale()) })
const ICON = { done: { i: CheckCircle2, c: 'var(--ok)' }, skipped: { i: SkipForward, c: 'var(--faint)' }, failed: { i: AlertTriangle, c: 'var(--bad)' }, running: { i: Loader2, c: 'var(--load)' } } as const

/** Stand der Umwandlung in M4B und der Kapitel-Ergänzung, mit Papierkorb der Originale. */
export function ConvertPanel() {
  const { data, reload } = useData<Overview>('/convert', 5000)
  const [msg, setMsg] = useState<string | null>(null)
  if (!data) return <div className="skeleton h-40 rounded-xl" />
  const limit = new Date(Date.now() - data.keep_days * 86400000).toISOString().slice(0, 10)
  const old = data.trash.filter(tx => tx.day <= limit)
  const trashBytes = data.trash.reduce((n, tx) => n + tx.bytes, 0)
  const purge = async () => {
    if (!await ask(t('Originale aus {{v0}} Tagen endgültig löschen? Das lässt sich nicht rückgängig machen.', { v0: old.length }), t('Endgültig löschen'))) return
    try { const r = await api<{ removed: number; bytes: number }>('/convert/purge', { method: 'POST' }); setMsg(t('{{v0}} Originaldateien endgültig gelöscht ({{v1}}).', { v0: r.removed, v1: gb(r.bytes) })); reload() }
    catch (e) { setMsg(t('Das hat nicht geklappt. {{v0}}', { v0: (e as Error).message })) }
  }
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      <div className="rounded-xl border border-line bg-surface p-4">
        <h3 className="text-base font-semibold">{t('MP3 zu M4B')}</h3>
        <dl className="mt-3 grid grid-cols-3 gap-3 text-sm">
          <div><dt className="text-faint">{t('Warten')}</dt><dd className="tabular text-xl font-bold">{data.pending.toLocaleString(locale())}</dd><dd className="tabular text-xs text-faint">{gb(data.pending_bytes)}</dd></div>
          <div><dt className="text-faint">{t('Umgewandelt')}</dt><dd className="tabular text-xl font-bold">{data.counts.done ?? 0}</dd></div>
          <div><dt className="text-faint">{t('Übersprungen')}</dt><dd className="tabular text-xl font-bold">{(data.counts.skipped ?? 0) + (data.counts.failed ?? 0)}</dd></div>
        </dl>
        {data.running && (
          <div className="mt-3 rounded-lg bg-surface-2 p-3 text-sm" aria-live="polite">
            <p className="flex items-center gap-2"><Loader2 size={14} className="animate-spin" aria-hidden />{t('Wandelt um:')}{' '}{data.running.title}</p>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-bg"><div className="h-full rounded-full transition-[width] duration-500" style={{ width: `${Math.round(data.running.pct * 100)}%`, background: 'var(--load)' }} /></div>
          </div>
        )}
        {data.jobs.length > 0 && (
          <ul className="mt-3 divide-y divide-line text-sm">
            {data.jobs.slice(0, 8).map(j => {
              const s = ICON[j.state as keyof typeof ICON] ?? ICON.skipped
              return (
                <li key={j.id} className="flex gap-2 py-2">
                  <s.i size={15} aria-hidden className={`mt-0.5 shrink-0 ${j.state === 'running' ? 'animate-spin' : ''}`} style={{ color: s.c }} />
                  <div className="min-w-0 flex-1">
                    <p className="flex flex-wrap items-center gap-2"><LangTag lang={j.lang} />
                      {j.series_id ? <Link to={`/serie/${j.series_id}`} className="min-w-0 truncate font-medium hover:text-accent">{j.title}</Link> : <span className="font-medium">{j.title}</span>}
                    </p>
                    <p className="break-words text-xs text-muted">{j.message ?? t('Läuft')}{j.out_bytes && j.src_bytes ? t(' · {{v0}} → {{v1}}', { v0: gb(j.src_bytes), v1: gb(j.out_bytes) }) : ''} · {fmtDateTime(j.started)}</p>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-3">
          <p className="text-sm text-muted">{t('Papierkorb der Originale:')}{' '}{gb(trashBytes)} {t('aus')}{' '}{data.trash.length} {t('Tagen, davon')}{' '}{old.length} {t('älter als')}{' '}{data.keep_days} {t('Tage.')}</p>
          <Button disabled={old.length === 0} onClick={purge} title={old.length ? undefined : t('Erst nach {{v0}} Tagen möglich', { v0: data.keep_days })}><Trash2 size={15} aria-hidden />{t('Ältere endgültig löschen')}</Button>
        </div>
        {msg && <p className="mt-2 text-sm" aria-live="polite">{msg}</p>}
      </div>
      <div className="rounded-xl border border-line bg-surface p-4">
        <h3 className="text-base font-semibold">{t('Kapitel ergänzen')}</h3>
        <dl className="mt-3 grid grid-cols-3 gap-3 text-sm">
          <div><dt className="text-faint">{t('Kommen in Frage')}</dt><dd className="tabular text-xl font-bold">{data.chapters.pending.toLocaleString(locale())}</dd></div>
          <div><dt className="text-faint">{t('Ergänzt')}</dt><dd className="tabular text-xl font-bold">{data.chapters.counts.done ?? 0}</dd></div>
          <div><dt className="text-faint">{t('Übersprungen')}</dt><dd className="tabular text-xl font-bold">{(data.chapters.counts.skipped ?? 0) + (data.chapters.counts.failed ?? 0)}</dd></div>
        </dl>
        <p className="mt-3 text-sm text-muted">{t('Betrifft Bücher aus einer einzigen Datei ohne Kapitelmarken, die eine Audible-Kennung haben. Die Kapitel werden in Audiobookshelf gesetzt, die Datei bleibt, wie sie ist.')}</p>
        {data.chapters.recent.length > 0 && (
          <ul className="mt-3 divide-y divide-line text-sm">
            {data.chapters.recent.slice(0, 8).map(r => {
              const s = ICON[r.state as keyof typeof ICON] ?? ICON.skipped
              return (
                <li key={r.book_id} className="flex gap-2 py-2">
                  <s.i size={15} aria-hidden className="mt-0.5 shrink-0" style={{ color: s.c }} />
                  <div className="min-w-0"><p className="truncate font-medium">{r.title}</p><p className="break-words text-xs text-muted">{r.message}</p></div>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </div>
  )
}
