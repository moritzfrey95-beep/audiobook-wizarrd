import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Bell, BookOpen, CalendarClock, CheckCircle2, LibraryBig, Sparkles, Star, type LucideIcon } from 'lucide-react'
import { api, useData } from './api'
import { locale, t } from './i18n'

export interface Note { id: number; kind: string; title: string; body: string | null; link: string | null; cover: string | null; created: string; read_at: string | null }
export interface Notes { items: Note[]; total: number; unread: number }

const KIND: Record<string, { icon: LucideIcon; color: string }> = {
  announced: { icon: CalendarClock, color: 'var(--info)' }, released: { icon: Sparkles, color: 'var(--warn)' }, new_book: { icon: Sparkles, color: 'var(--warn)' },
  available: { icon: CheckCircle2, color: 'var(--ok)' }, new_series: { icon: LibraryBig, color: 'var(--accent)' }, epub: { icon: BookOpen, color: 'var(--muted)' },
  rate: { icon: Star, color: 'var(--warn)' },
}

export function ago(iso: string) {
  const min = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000))
  if (min < 1) return t('gerade eben')
  if (min < 60) return t('vor {{n}} min', { n: min })
  if (min < 1440) return t('vor {{n}} h', { n: Math.round(min / 60) })
  if (min < 2880) return t('gestern')
  return new Date(iso).toLocaleDateString(locale(), { day: '2-digit', month: '2-digit', year: 'numeric' })
}

export function NoteRow({ n, onOpen }: { n: Note; onOpen?: () => void }) {
  const k = KIND[n.kind] ?? { icon: Bell, color: 'var(--muted)' }
  const Icon = k.icon
  const body = (
    <>
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg" style={{ background: `color-mix(in srgb, ${k.color} 14%, transparent)`, color: k.color }}><Icon size={17} aria-hidden /></span>
      <span className="min-w-0 flex-1">
        <span className="flex items-baseline justify-between gap-3">
          <span className={`text-sm ${n.read_at ? 'font-medium' : 'font-bold'}`}>{n.title}</span>
          <span className="tabular shrink-0 text-xs text-faint">{ago(n.created)}</span>
        </span>
        {n.body && <span className="mt-0.5 block break-words text-sm text-muted">{n.body}</span>}
      </span>
      {!n.read_at && <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-accent-strong" aria-label="ungelesen" />}
    </>
  )
  const cls = 'flex w-full items-start gap-3 rounded-lg p-2.5 text-left transition-colors hover:bg-surface-2'
  return n.link ? <Link to={n.link} onClick={onOpen} className={cls}>{body}</Link> : <div className={cls}>{body}</div>
}

/** Glocke in der Kopfzeile: Zähler der ungelesenen und die neusten Benachrichtigungen, auf jeder Seite. */
export function NotificationBell() {
  const { data, reload } = useData<Notes>('/notifications?limit=8', 30000)
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false) }
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', away); window.addEventListener('keydown', key)
    return () => { document.removeEventListener('mousedown', away); window.removeEventListener('keydown', key) }
  }, [open])
  const unread = data?.unread ?? 0
  const read = async (ids?: number[]) => { await api('/notifications/read', { method: 'POST', body: JSON.stringify(ids ? { ids } : {}) }); reload() }
  return (
    <div ref={box} className="relative shrink-0">
      <button type="button" onClick={() => { setOpen(!open); if (!open) reload() }} aria-expanded={open} aria-haspopup="true"
        aria-label={unread ? t('Benachrichtigungen, {{v0}} ungelesen', { v0: unread }) : t('Benachrichtigungen')} title={t('Benachrichtigungen')}
        className="relative flex h-11 w-11 items-center justify-center rounded-lg text-muted transition-colors hover:bg-surface hover:text-text">
        <Bell size={18} aria-hidden />
        {unread > 0 && <span className="tabular absolute right-1 top-1 min-w-5 rounded-full bg-accent-strong px-1 text-center text-[11px] font-bold leading-5 text-on-accent">{unread > 99 ? '99+' : unread}</span>}
      </button>
      {open && (
        <div role="dialog" aria-label={t('Neuste Benachrichtigungen')}
          className="fixed inset-x-3 top-16 z-40 rounded-2xl border border-line bg-surface p-2 shadow-2xl sm:absolute sm:inset-x-auto sm:right-0 sm:top-12 sm:w-[26rem]">
          <div className="flex items-center justify-between gap-2 px-2.5 py-1.5">
            <h2 className="text-sm font-semibold">{t('Benachrichtigungen')}</h2>
            {unread > 0 && <button type="button" onClick={() => read()} className="min-h-9 text-xs font-semibold text-accent underline-offset-4 hover:underline">{t('Alle als gelesen markieren')}</button>}
          </div>
          {!data?.items.length
            ? <p className="px-2.5 py-6 text-center text-sm text-muted">{t('Noch keine Benachrichtigungen. Markiere Serien oder Autoren mit der Glocke, dann erfährst du hier von neuen Bänden.')}</p>
            : <ul className="max-h-[60vh] overflow-y-auto">{data.items.map(n => <li key={n.id}><NoteRow n={n} onOpen={() => { setOpen(false); if (!n.read_at) read([n.id]) }} /></li>)}</ul>}
          <div className="mt-1 grid grid-cols-2 gap-1">
            <Link to="/benachrichtigungen" onClick={() => setOpen(false)} className="flex min-h-10 items-center justify-center rounded-lg text-sm font-semibold text-accent hover:bg-surface-2">{t('Alle Meldungen')}</Link>
            <Link to="/kalender" onClick={() => setOpen(false)} className="flex min-h-10 items-center justify-center rounded-lg text-sm font-semibold text-accent hover:bg-surface-2">{t('Kalender')}</Link>
          </div>
        </div>
      )}
    </div>
  )
}
