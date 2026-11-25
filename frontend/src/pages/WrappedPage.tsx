import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ChevronLeft, ChevronRight, X } from 'lucide-react'
import { useData } from '../api'
import { ErrorBox, Page } from '../ui'
import { useAdmin } from '../session'
import { locale, t } from '../i18n'

interface Named { name: string; hours: number }
interface W {
  year: number; years: number[]; complete: boolean; user: string | null; users: { abs_user_id: string; name: string }[]
  hours: number; prev_hours: number; days: number; sessions: number; books: number; finished: number; streak: number
  top_books: { item_id: string; title: string; author: string; hours: number }[]
  top_series: { name: string; author: string; hours: number; books: number; series: boolean; item_id: string }[]; top_authors: Named[]; top_narrators: Named[]
  best_month: Named | null; months: { label: string; hours: number }[]; weekday: string | null; hour: number | null
  longest: { title: string; hours: number; day: string } | null; best_day: { day: string; hours: number } | null
  fastest: { item_id: string; title: string; days: number; hours: number | null } | null; avg_days: number | null
  series_done: { series_id: number; name: string; books: number; item_id: string }[]; langs: Record<string, number>; authors_total: number
}

const n = (v: number) => v.toLocaleString(locale(), { maximumFractionDigits: 1 })
const day = (iso: string) => new Date(iso).toLocaleDateString(locale(), { day: 'numeric', month: 'long' })
const HUES = ['#7c3aed', '#db2777', '#ea580c', '#0d9488', '#2563eb', '#65a30d', '#9333ea', '#0891b2', '#c2410c']

function Card({ i, kicker, children }: { i: number; kicker: string; children: React.ReactNode }) {
  const hue = HUES[i % HUES.length]
  return (
    <section className="relative flex h-full w-full shrink-0 snap-center flex-col justify-center overflow-hidden rounded-3xl p-7 text-white sm:p-10"
      style={{ background: `radial-gradient(120% 90% at 10% 0%, ${hue} 0%, #111827 75%)` }}>
      <p className="mb-4 text-sm font-semibold uppercase tracking-widest text-white/70">{kicker}</p>
      {children}
    </section>
  )
}

const Big = ({ children }: { children: React.ReactNode }) => <p className="font-[family-name:var(--font-display)] text-5xl font-extrabold leading-none sm:text-7xl">{children}</p>
const Line = ({ children }: { children: React.ReactNode }) => <p className="mt-4 text-lg text-white/85 sm:text-xl">{children}</p>

function Ranking({ items, cover }: { items: { title: string; sub?: string; hours: number; item_id?: string }[]; cover?: boolean }) {
  return (
    <ol className="mt-2 space-y-3">
      {items.map((x, k) => (
        <li key={k} className="flex items-center gap-3">
          <span className="w-6 text-right text-2xl font-extrabold text-white/60">{k + 1}</span>
          {cover && x.item_id && <img src={`/api/cover/${x.item_id}`} alt="" className="h-12 w-12 rounded-md object-cover" loading="lazy" />}
          <span className="min-w-0 flex-1"><span className="block truncate text-lg font-semibold">{x.title}</span>{x.sub && <span className="block truncate text-sm text-white/70">{x.sub}</span>}</span>
          <span className="tabular text-white/80">{t('{{v0}} h', { v0: n(x.hours) })}</span>
        </li>
      ))}
    </ol>
  )
}

function cards(w: W): { kicker: string; body: React.ReactNode }[] {
  const out: { kicker: string; body: React.ReactNode }[] = []
  const max = Math.max(...w.months.map(m => m.hours), 1)
  out.push({ kicker: t('Dein Hörjahr {{v0}}', { v0: w.year }), body: <>
    <Big>{t('{{v0}} Stunden', { v0: n(w.hours) })}</Big>
    <Line>{t('an {{v0}} Tagen, in {{v1}} Sitzungen.', { v0: w.days, v1: n(w.sessions) })}{!w.complete && ` ${t('Das Jahr läuft noch.')}`}</Line>
    {w.prev_hours > 0 && <Line>{w.hours >= w.prev_hours ? t('Das sind {{v0}} Stunden mehr als {{v1}}.', { v0: n(w.hours - w.prev_hours), v1: w.year - 1 }) : t('{{v0}} waren es {{v1}} Stunden.', { v0: w.year - 1, v1: n(w.prev_hours) })}</Line>}
  </> })
  if (w.top_books.length) out.push({ kicker: t('Dein Buch des Jahres'), body: <>
    <div className="flex flex-col items-start gap-5 sm:flex-row sm:items-center">
      <img src={`/api/cover/${w.top_books[0].item_id}`} alt="" className="h-40 w-40 rounded-xl object-cover shadow-2xl sm:h-52 sm:w-52" />
      <div><p className="font-[family-name:var(--font-display)] text-3xl font-extrabold sm:text-4xl">{w.top_books[0].title}</p>
        <Line>{w.top_books[0].author} · {t('{{v0}} Stunden', { v0: n(w.top_books[0].hours) })}</Line></div>
    </div>
  </> })
  if ((w.top_series ?? []).length > 1) out.push({ kicker: t('Am meisten gehört'), body: <Ranking cover items={w.top_series.map(x => ({
    title: x.name, sub: x.series ? (x.books > 1 ? `${x.author} · ${t('{{n}} Bände', { n: x.books })}` : x.author) : `${x.author} · ${t('Einzelbuch')}`, hours: x.hours, item_id: x.item_id }))} /> })
  if (w.top_authors.length) out.push({ kicker: t('Deine Autoren'), body: <>
    <Big>{w.top_authors[0].name}</Big>
    <Line>{t('{{v0}} Stunden. Insgesamt hast du {{v1}} Autoren gehört.', { v0: n(w.top_authors[0].hours), v1: w.authors_total })}</Line>
    <Ranking items={w.top_authors.slice(1).map(a => ({ title: a.name, hours: a.hours }))} />
  </> })
  if (w.top_narrators.length) out.push({ kicker: t('Die Stimme deines Jahres'), body: <>
    <Big>{w.top_narrators[0].name}</Big>
    <Line>{t('hat dir {{v0}} Stunden vorgelesen.', { v0: n(w.top_narrators[0].hours) })}</Line>
    <Ranking items={w.top_narrators.slice(1).map(a => ({ title: a.name, hours: a.hours }))} />
  </> })
  out.push({ kicker: t('Fertig gehört'), body: <>
    <Big>{t('{{v0}} Bücher', { v0: w.finished })}</Big>
    {w.avg_days != null && <Line>{t('Im Schnitt {{v0}} Tage vom ersten Hören bis zum Ende.', { v0: w.avg_days })}</Line>}
    {w.fastest && <Line>{t('Am schnellsten: «{{v0}}» in {{v1}}.', { v0: w.fastest.title, v1: w.fastest.days === 1 ? t('einem Tag') : t('{{n}} Tagen', { n: w.fastest.days }) })}</Line>}
  </> })
  if (w.series_done.length) out.push({ kicker: t('Serien abgeschlossen'), body: <>
    <Big>{w.series_done.length === 1 ? w.series_done[0].name : t('{{v0}} Serien', { v0: w.series_done.length })}</Big>
    <ul className="mt-4 space-y-2 text-lg">{w.series_done.map(s => <li key={s.series_id}>{t('{{v0}}: alle {{v1}} Bände', { v0: s.name, v1: s.books })}</li>)}</ul>
  </> })
  if (w.best_month) out.push({ kicker: t('Dein Jahr in Monaten'), body: <>
    <p className="text-2xl font-bold">{t('Stärkster Monat: {{v0}} mit {{v1}} Stunden', { v0: w.best_month.name, v1: n(w.best_month.hours) })}</p>
    <div className="mt-6 flex h-44 items-end gap-1.5" aria-hidden>
      {w.months.map(m => <div key={m.label} className="flex flex-1 flex-col items-center gap-1">
        <div className="w-full rounded-t bg-white/85" style={{ height: `${Math.max(2, (m.hours / max) * 150)}px` }} />
        <span className="text-[10px] text-white/70">{m.label}</span></div>)}
    </div>
  </> })
  if (w.weekday) out.push({ kicker: t('Deine Hörgewohnheiten'), body: <>
    <Big>{w.weekday}</Big>
    <Line>{t('ist dein Hörtag, am liebsten um {{v0}} Uhr.', { v0: w.hour })}</Line>
    {w.streak > 1 && <Line>{t('Längste Serie: {{v0}} Tage am Stück.', { v0: w.streak })}</Line>}
    {w.best_day && <Line>{t('Rekordtag: {{v0}} mit {{v1}} Stunden.', { v0: day(w.best_day.day), v1: n(w.best_day.hours) })}</Line>}
  </> })
  const de = w.langs.de ?? 0, en = w.langs.en ?? 0
  if (de + en > 0) out.push({ kicker: t('Deutsch und Englisch'), body: <>
    <Big>{t('{{v0}} % Englisch', { v0: Math.round((en / (de + en)) * 100) })}</Big>
    <Line>{t('{{v0}} Stunden Englisch, {{v1}} Stunden Deutsch.', { v0: n(en), v1: n(de) })}</Line>
  </> })
  out.push({ kicker: t('Das war {{v0}}', { v0: w.year }), body: <>
    <Big>{t('Danke fürs Zuhören!')}</Big>
    <Line>{t('{{v0}} Bücher, {{v1}} Stunden. Auf ein neues Hörjahr.', { v0: w.books, v1: n(w.hours) })}</Line>
    <p className="mt-8"><Link to="/hoerverlauf" className="rounded-lg bg-white px-4 py-2.5 font-semibold text-gray-900">{t('Zum Hörverlauf')}</Link></p>
  </> })
  return out
}

/** Jahresrückblick als Kartenfolge: wischen, Pfeiltasten oder Knöpfe. */
export default function WrappedPage() {
  const admin = useAdmin()
  const [params, setParams] = useSearchParams()
  const year = Number(params.get('jahr')) || 0
  const user = params.get('konto') ?? ''
  const { data, error, reload } = useData<W>(`/wrapped?year=${year}&user=${encodeURIComponent(user)}`)
  const strip = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState(0)
  const list = data ? cards(data) : []
  const go = (k: number) => { const el = strip.current; if (el) el.scrollTo({ left: Math.max(0, Math.min(list.length - 1, k)) * el.clientWidth, behavior: 'smooth' }) }
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === 'ArrowRight') go(pos + 1); if (e.key === 'ArrowLeft') go(pos - 1) }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  })
  if (error && !data) return <Page title={t('Jahresrückblick')}><ErrorBox message={error} onRetry={reload} /></Page>
  if (!data) return <Page title={t('Jahresrückblick')}><div className="skeleton h-[70vh] rounded-3xl" /></Page>
  const set = (k: string, v: string) => { const p = new URLSearchParams(params); if (v) p.set(k, v); else p.delete(k); setParams(p); setPos(0); strip.current?.scrollTo({ left: 0 }) }
  return (
    <div className="mx-auto flex h-full max-w-xl flex-col gap-3 px-4 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <select className="min-h-9 rounded-lg border border-line bg-surface px-2 text-sm" value={data.year} onChange={e => set('jahr', e.target.value)} aria-label={t('Jahr')}>
          {(data.years.length ? data.years : [data.year]).map(y => <option key={y} value={y}>{y}</option>)}
        </select>
        {admin && (
          <select className="min-h-9 rounded-lg border border-line bg-surface px-2 text-sm" value={user} onChange={e => set('konto', e.target.value)} aria-label={t('Konto')}>
            <option value="">{t('Alle zusammen')}</option>
            {data.users.map(u => <option key={u.abs_user_id} value={u.abs_user_id}>{u.name}</option>)}
          </select>
        )}
        <Link to="/hoerverlauf" className="ml-auto inline-flex min-h-9 items-center gap-1 text-sm text-muted hover:text-text"><X size={16} aria-hidden />{t('Schliessen')}</Link>
      </div>
      {data.hours === 0
        ? <p className="rounded-2xl border border-line bg-surface p-6 text-sm text-muted">{t('In diesem Jahr gibt es noch keinen Hörverlauf.')}</p>
        : (
          <>
            <div ref={strip} className="flex h-[72vh] min-h-[440px] snap-x snap-mandatory gap-0 overflow-x-auto overscroll-x-contain [scrollbar-width:none]"
              onScroll={e => { const el = e.currentTarget; setPos(Math.round(el.scrollLeft / el.clientWidth)) }}>
              {list.map((c, i) => <Card key={i} i={i} kicker={c.kicker}>{c.body}</Card>)}
            </div>
            <div className="flex items-center justify-between gap-3">
              <button type="button" onClick={() => go(pos - 1)} disabled={pos === 0} className="flex h-11 w-11 items-center justify-center rounded-full border border-line disabled:opacity-30" aria-label={t('Zurück')}><ChevronLeft size={20} aria-hidden /></button>
              <div className="flex flex-wrap justify-center gap-1.5" aria-hidden>{list.map((_, i) => <span key={i} className={`h-1.5 rounded-full transition-all ${i === pos ? 'w-6 bg-accent' : 'w-1.5 bg-line'}`} />)}</div>
              <button type="button" onClick={() => go(pos + 1)} disabled={pos >= list.length - 1} className="flex h-11 w-11 items-center justify-center rounded-full border border-line disabled:opacity-30" aria-label={t('Weiter')}><ChevronRight size={20} aria-hidden /></button>
            </div>
          </>
        )}
    </div>
  )
}
