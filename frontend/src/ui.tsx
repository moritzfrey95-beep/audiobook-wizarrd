import { useEffect, useState, type ReactNode } from 'react'
import {
  AlertTriangle, BookOpen, CalendarClock, CheckCircle2, CircleDashed, CircleHelp, Download, EyeOff, Radar, SearchX, ShieldQuestion,
  type LucideIcon,
} from 'lucide-react'
import type { BookState, Edition, Flag } from './api'
import { t } from './i18n'

/** Indikatoren: immer Symbol plus Text, nie nur Farbe. */
export const STATUS: Record<Flag | BookState, { label: string; icon: LucideIcon; color: string; pulse?: boolean }> = {
  downloading: { label: t('Lädt herunter'), icon: Download, color: 'var(--load)', pulse: true },
  review: { label: t('In Prüfung'), icon: ShieldQuestion, color: 'var(--review)' },
  failed: { label: t('Fehlgeschlagen'), icon: AlertTriangle, color: 'var(--bad)' },
  searching: { label: t('Sucht aktiv'), icon: Radar, color: 'var(--search)', pulse: true },
  incomplete: { label: t('Unvollständig'), icon: CircleDashed, color: 'var(--warn)' },
  missing: { label: t('Fehlt'), icon: CircleDashed, color: 'var(--warn)' },
  upcoming: { label: t('Neues Buch kommt'), icon: CalendarClock, color: 'var(--info)' },
  complete: { label: t('Abgeschlossen'), icon: CheckCircle2, color: 'var(--ok)' },
  owned: { label: t('Vorhanden'), icon: CheckCircle2, color: 'var(--ok)' },
  unknown: { label: t('Ungeprüft'), icon: CircleHelp, color: 'var(--faint)' },
  single: { label: t('Einzelbuch'), icon: BookOpen, color: 'var(--faint)' },
  absent: { label: t('Nicht bei Audible'), icon: SearchX, color: 'var(--faint)' },
  unmonitored: { label: t('Nicht überwacht'), icon: EyeOff, color: 'var(--faint)' },
}

/** «Unvollständig» zeigt schon der Zähler (4/5); der Status bleibt nur zum Filtern. */
export const shownFlags = <T extends Flag>(flags: T[]): T[] => flags.filter(f => f !== 'incomplete')

export function Badge({ status, label, solid }: { status: Flag | BookState; label?: string; solid?: boolean }) {
  const s = STATUS[status]
  const Icon = s.icon
  return (
    <span
      className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-semibold"
      style={solid
        ? { background: 'rgba(8,10,15,.82)', color: s.color, backdropFilter: 'blur(6px)' }
        : { background: `color-mix(in srgb, ${s.color} 14%, transparent)`, color: s.color }}
    >
      <Icon size={13} strokeWidth={2.4} aria-hidden className={s.pulse ? 'pulse' : ''} />
      {label ?? s.label}
    </span>
  )
}

export function Cover({ src, alt, className = '' }: { src: string | null; alt: string; className?: string }) {
  const [failed, setFailed] = useState(false)
  return (
    <div className={`relative aspect-square overflow-hidden bg-surface-2 ${className}`}>
      {src && !failed ? (
        <img src={src} alt={alt} loading="lazy" decoding="async" onError={() => setFailed(true)}
          className="h-full w-full object-cover" />
      ) : (
        <div className="flex h-full w-full flex-col items-center justify-center gap-2 p-3 text-center text-faint">
          <BookOpen size={28} aria-hidden />
          <span className="line-clamp-2 text-xs">{alt}</span>
        </div>
      )}
    </div>
  )
}

export function LangTag({ lang }: { lang: string }) {
  return (
    <span className="inline-flex h-5 min-w-7 items-center justify-center rounded bg-surface-2 px-1 text-[11px] font-bold uppercase tracking-wide text-muted">
      {lang}
    </span>
  )
}

/** Eine Zeile pro Sprachfassung: Kürzel, Fortschrittsbalken, vorhanden/gesamt. */
export function EditionRow({ e }: { e: Edition }) {
  const pct = e.audible_asin && e.total ? Math.round((e.owned / e.total) * 100) : 0
  const color = !e.monitored ? 'var(--faint)' : STATUS[e.flags[0] ?? 'unknown'].color
  const known = !!e.audible_asin
  return (
    <div className="flex items-center gap-2 text-xs" title={`${e.name}${e.variant ? t(' · {{v0}}', { v0: e.variant }) : ''}${e.monitored ? '' : t(' (nicht überwacht)')}`}>
      <LangTag lang={e.lang} />
      {e.variant && <span className="max-w-24 shrink-0 truncate text-faint">{e.variant}</span>}
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-2">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="tabular w-14 text-right font-medium text-muted">
        {e.owned}/{known ? e.total : '?'}{e.upcoming > 0 && <span style={{ color: 'var(--info)' }}> +{e.upcoming}</span>}
      </span>
    </div>
  )
}

export function Page({ title, subtitle, actions, children }: { title: string; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <main className="mx-auto w-full max-w-[1600px] px-4 pb-16 pt-6 sm:px-6 lg:px-8">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold sm:text-3xl">{title}</h1>
          {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
        </div>
        {actions}
      </div>
      {children}
    </main>
  )
}

export function Chip({ active, onClick, children, count }: { active: boolean; onClick: () => void; children: ReactNode; count?: number }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={active}
      className={`inline-flex min-h-9 items-center gap-2 whitespace-nowrap rounded-full border px-3.5 text-sm font-medium transition-colors duration-150 ${
        active ? 'border-accent bg-accent text-on-accent' : 'border-line bg-surface text-muted hover:border-faint hover:text-text'}`}>
      {children}
      {count !== undefined && <span className={`tabular text-xs ${active ? 'opacity-85' : 'text-faint'}`}>{count}</span>}
    </button>
  )
}

/** Zähler-Abzeichen, überall gleich (Leiste, Seitenleiste, Register). */
/** Zähler-Abzeichen; «user» färbt es, wenn etwas davon Bücher betrifft, die Benutzer hinzugefügt haben. */
export function Count({ n, label, tone }: { n: number; label?: string; tone?: 'user' }) {
  if (n <= 0) return null
  return <span className={`tabular min-w-5 rounded-full px-1.5 text-center text-[11px] font-bold leading-5 text-on-accent ${tone === 'user' ? 'bg-user-strong' : 'bg-accent-strong'}`}
    aria-label={label}>{n > 99 ? '99+' : n}</span>
}

/** Kleines Schild «Für Bea, Kurt» an Büchern, die Benutzer hinzugefügt haben. */
export function UserTag({ users }: { users?: string[] }) {
  if (!users?.length) return null
  return <span className="inline-flex max-w-full items-center gap-1 rounded-full border border-user px-2 text-xs font-semibold leading-5 text-user"
    title={t('Hinzugefügt von {{v0}}', { v0: users.join(', ') })}><span className="truncate">{t('Für')}{' '}{users.join(', ')}</span></span>
}

/** Register mit Unterstrich. Chips bleiben für Filter, Register wechseln die Ansicht (Regel seit 20.11.). */
export function Tabs<T extends string>({ value, onChange, items, label }: {
  value: T; onChange: (v: T) => void; items: { key: T; label: string; count?: number; tone?: 'user' }[]; label: string
}) {
  return (
    <div role="tablist" aria-label={label} className="mb-5 flex gap-5 overflow-x-auto border-b border-line [scrollbar-width:none]">
      {items.map(i => (
        <button key={i.key} type="button" role="tab" aria-selected={value === i.key} onClick={() => onChange(i.key)} className={tabClass(value === i.key)}>
          {i.label}
          {i.count !== undefined && i.count > 0 && <span className={`tabular rounded-full px-1.5 text-xs leading-5 ${i.tone === 'user' ? 'bg-user-strong font-semibold text-on-accent' : 'bg-surface-2 text-muted'}`}>{i.count}</span>}
        </button>
      ))}
    </div>
  )
}

export const tabClass = (active: boolean) => `-mb-px inline-flex min-h-11 shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 text-sm font-medium transition-colors duration-150 ${
  active ? 'border-accent text-text' : 'border-transparent text-muted hover:text-text'}`

export function Button({ children, onClick, variant = 'ghost', disabled, title, type = 'button', autoFocus }: {
  children: ReactNode; onClick?: () => void; variant?: 'primary' | 'ghost'; disabled?: boolean; title?: string; type?: 'button' | 'submit'; autoFocus?: boolean
}) {
  const style = variant === 'primary'
    ? 'bg-accent-strong text-on-accent hover:brightness-110'
    : 'border border-line bg-surface text-text hover:border-faint'
  return (
    <button type={type} onClick={onClick} disabled={disabled} title={title} autoFocus={autoFocus}
      className={`inline-flex min-h-10 shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-lg px-4 text-sm font-semibold transition duration-150 disabled:opacity-45 disabled:hover:brightness-100 ${style}`}>
      {children}
    </button>
  )
}

export function ErrorBox({ message, onRetry, plain }: { message: string; onRetry?: () => void; plain?: boolean }) {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-3 rounded-xl border border-line bg-surface p-4 text-sm">
      <AlertTriangle size={18} style={{ color: 'var(--bad)' }} aria-hidden />
      <span className="flex-1">{plain ? message : t('Die Daten konnten nicht geladen werden. {{v0}}', { v0: message })}</span>
      {onRetry && <Button onClick={onRetry}>{t('Erneut laden')}</Button>}
    </div>
  )
}

export function Empty({ title, text, action }: { title: string; text: string; action?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-line px-6 py-14 text-center">
      <h2 className="text-lg font-semibold">{title}</h2>
      <p className="mx-auto mt-1 max-w-md text-sm text-muted">{text}</p>
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
  )
}

export const GRID = 'card-grid grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-7'

export function SkeletonGrid({ n = 14 }: { n?: number }) {
  return (
    <div className={GRID} aria-busy="true" aria-label={t('Lädt')}>
      {Array.from({ length: n }, (_, i) => (
        <div key={i} className="overflow-hidden rounded-xl border border-line bg-surface">
          <div className="skeleton aspect-square" />
          <div className="space-y-2 p-3"><div className="skeleton h-4 rounded" /><div className="skeleton h-3 w-2/3 rounded" /></div>
        </div>
      ))}
    </div>
  )
}

export function Section({ title, hint, children }: { title: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <section className="mt-8">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold">{title}</h2>
        {hint && <span className="text-sm text-muted">{hint}</span>}
      </div>
      {children}
    </section>
  )
}


// ---------- Rückfragen und Hinweise ----------
// Eigene Dialoge statt confirm()/alert() des Browsers: die erscheinen in manchen Umgebungen (eingebettete Ansicht,
// App-Fenster) gar nicht, und der Knopf tut dann scheinbar nichts.
interface Ask { text: string; confirm: string; info: boolean; resolve: (ok: boolean) => void; input?: (value: string) => void }
let openDialog: ((a: Ask) => void) | null = null

/** Rückfrage mit «Abbrechen» und Bestätigung. Liefert true, wenn bestätigt wurde. */
export function ask(text: string, confirm = t('Ja, ausführen')): Promise<boolean> {
  return new Promise(resolve => (openDialog ? openDialog({ text, confirm, info: false, resolve }) : resolve(false)))
}

/** Hinweis mit einem einzigen Knopf. */
export function tell(text: string): Promise<boolean> {
  return new Promise(resolve => (openDialog ? openDialog({ text, confirm: t('Verstanden'), info: true, resolve }) : resolve(true)))
}

/** Rückfrage mit einem Eingabefeld. Liefert den eingegebenen Text oder null bei Abbruch. */
export function prompt(text: string, confirm = t('Speichern')): Promise<string | null> {
  return new Promise(resolve => {
    let value = ''
    if (!openDialog) { resolve(null); return }
    openDialog({ text, confirm, info: false, input: v => { value = v }, resolve: ok => resolve(ok && value.trim() ? value : null) })
  })
}

export function DialogHost() {
  const [a, setA] = useState<Ask | null>(null)
  useEffect(() => { openDialog = setA; return () => { openDialog = null } }, [])
  useEffect(() => {
    if (!a) return
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { a.resolve(a.info); setA(null) } }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [a])
  if (!a) return null
  const close = (ok: boolean) => { a.resolve(ok); setA(null) }
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-4 sm:items-center" onClick={() => close(a.info)}>
      <div role="alertdialog" aria-modal="true" aria-describedby="abw-dialog-text" onClick={e => e.stopPropagation()}
        className="w-full max-w-md rounded-2xl border border-line bg-surface p-5 shadow-2xl">
        <p id="abw-dialog-text" className="whitespace-pre-line break-words text-sm leading-relaxed">{a.text}</p>
        {a.input && (
          <input type="text" autoFocus autoComplete="off" aria-label={t('Eingabe')} onChange={e => a.input!(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') close(true) }} className="mt-3 h-11 w-full rounded-lg border border-line bg-bg px-3 text-sm" />
        )}
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          {!a.info && <Button onClick={() => close(false)}>{t('Abbrechen')}</Button>}
          <Button variant="primary" autoFocus={!a.input} onClick={() => close(true)}>{a.confirm}</Button>
        </div>
      </div>
    </div>
  )
}
