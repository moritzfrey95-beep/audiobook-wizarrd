import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ChevronLeft, ChevronRight, Sparkles } from 'lucide-react'
import { fmtNum, useData } from '../api'
import { Button, Chip, Cover, Empty, ErrorBox, Page, Section } from '../ui'
import { Bars, Tile } from './StatsPage'
import { useAdmin } from '../session'
import { locale, t } from '../i18n'

type Period = 'week' | 'month' | 'year'
interface Meta { item_id: string; title: string; author: string }
interface Account { abs_user_id: string; name: string; abw_id: number | null; seconds: number; prev_seconds: number; books: number; days: number; sessions: number; finished: number; last: string | null }
interface Listening {
  range: { period: Period; offset: number; start: string; end: string; label: string; days: number }
  users: Account[]; user: string
  summary: {
    seconds: number; prev_seconds: number; sessions: number; books: number; days: number; finished: number; listeners: number; corrected: number
    streak: number; current_streak: number | null; longest: (Meta & { seconds: number; started: string }) | null
    best_day: { day: string; seconds: number } | null; best_slot: { key: string; seconds: number } | null
    weekday: string | null; hour: number | null; lang: Record<string, number>
  }
  timeline: { key: string; label: string; seconds: number }[]
  heat: number[][]
  books: (Meta & { seconds: number; sessions: number; first: string; last: string; listeners: string[]; progress: number | null; finished: boolean })[]
  authors: { name: string; seconds: number }[]; devices: { name: string; seconds: number }[]
  finished: (Meta & { finished_at: string; name: string })[]
  stale: (Meta & { progress: number; last: string })[]
  history: (Meta & { id: string; name: string; started: string; updated: string; seconds: number; raw_seconds: number | null; pos_start: number | null; pos_end: number | null; duration: number | null; device: string })[]
  history_total: number
}

const PERIODS: [Period, string][] = [['week', t('Woche')], ['month', t('Monat')], ['year', t('Jahr')]]
const PREV: Record<Period, string> = { week: t('Vorwoche'), month: t('Vormonat'), year: t('Vorjahr') }
const DAYS = [t('Mo'), t('Di'), t('Mi'), t('Do'), t('Fr'), t('Sa'), t('So')]
const MONTHS = [t('Januar'), t('Februar'), t('März'), t('April'), t('Mai'), t('Juni'), t('Juli'), t('August'), t('September'), t('Oktober'), t('November'), t('Dezember')]

/** Hörzeit lesbar: «45 min», «12 h 05 min», ab 100 Stunden nur noch «981 h». */
export const fmtDur = (sec: number) => {
  const min = Math.round(sec / 60)
  if (min < 60) return `${min} min`
  const h = Math.floor(min / 60)
  return h >= 100 ? `${fmtNum(h)} h` : `${h} h ${String(min % 60).padStart(2, '0')} min`
}
/** Für die grossen Kacheln: ab 10 Stunden ohne Minuten, damit die Zahl auch auf dem Handy in eine Zeile passt. */
const fmtBig = (sec: number) => sec >= 36000 ? `${fmtNum(Math.round(sec / 3600))} h` : fmtDur(sec)
const hours = (sec: number) => Math.round(sec / 360) / 10
const fmtDay = (d: string) => new Date(d + 'T12:00:00').toLocaleDateString(locale(), { weekday: 'short', day: 'numeric', month: 'numeric', year: 'numeric' })
const fmtWhen = (iso: string) => new Date(iso).toLocaleString(locale(), { weekday: 'short', day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' })
const pct = (x: number | null | undefined) => `${Math.round((x ?? 0) * 100)} %`

function trend(now: number, prev: number, label: string) {
  if (!prev) return now ? t('{{v0}}: nichts gehört', { v0: label }) : undefined
  const d = Math.round(((now - prev) / prev) * 100)
  return t('{{v0}}{{v1}} % gegenüber {{v2}} ({{v3}})', { v0: d > 0 ? '+' : '', v1: d, v2: label, v3: fmtDur(prev) })
}

function BookLine({ b, right, sub }: { b: Meta; right?: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <li className="flex items-center gap-3 border-t border-line px-4 py-2.5 first:border-t-0">
      <Cover src={`/api/cover/${b.item_id}`} alt={b.title} className="w-11 shrink-0 rounded-md" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{b.title}</p>
        <p className="truncate text-xs text-muted">{b.author}{sub && <> · {sub}</>}</p>
      </div>
      {right && <div className="tabular shrink-0 text-right text-sm text-muted">{right}</div>}
    </li>
  )
}

/** Hörzeit pro Tag oder Monat als Säulen; eine Reihe, darum ohne Legende. */
function Timeline({ rows, period }: { rows: Listening['timeline']; period: Period }) {
  const max = Math.max(1, ...rows.map(r => r.seconds))
  const tip = (r: Listening['timeline'][number]) => `${period === 'year' ? `${MONTHS[Number(r.key.slice(5)) - 1]} ${r.key.slice(0, 4)}` : fmtDay(r.key)}: ${r.seconds ? fmtDur(r.seconds) : t('nichts')}`
  return (
    <div className="overflow-x-auto rounded-xl border border-line bg-surface p-4">
      <div className={period === 'month' ? 'min-w-[620px]' : ''}>
        <div className="flex h-44 items-end gap-1" role="img" aria-label={t('Hörzeit: {{v0}}', { v0: rows.map(tip).join(', ') })}>
          {rows.map(r => (
            <div key={r.key} title={tip(r)} className="group flex h-full flex-1 flex-col justify-end">
              {period !== 'month' && <span className="tabular mb-1 text-center text-[10px] text-faint">{r.seconds ? (hours(r.seconds) >= 10 ? Math.round(hours(r.seconds)) : hours(r.seconds)) : ''}</span>}
              <span className="rounded-t-[4px] transition-opacity group-hover:opacity-80"
                style={{ height: r.seconds ? `max(3px, ${(r.seconds / max) * 85}%)` : '0', background: 'var(--accent)' }} />
            </div>
          ))}
        </div>
        <div className="mt-2 flex gap-1 border-t border-line pt-1.5 text-[10px] text-faint">{rows.map(r => <span key={r.key} className="tabular flex-1 text-center">{r.label}</span>)}</div>
      </div>
      <p className="mt-2 text-xs text-faint">{t('Stunden; Sitzungen zählen am Tag, an dem sie begonnen haben')}</p>
    </div>
  )
}

/** Wochentag mal Stunde: je dunkler, desto mehr Hörzeit (eine Farbe, nur die Deckkraft ändert sich). */
function Heat({ heat }: { heat: number[][] }) {
  const max = Math.max(1, ...heat.flat())
  return (
    <div className="overflow-x-auto rounded-xl border border-line bg-surface p-4">
      <div className="min-w-[560px]">
        <div className="grid gap-[2px]" style={{ gridTemplateColumns: '2rem repeat(24, minmax(0, 1fr))' }}>
          <span />
          {Array.from({ length: 24 }, (_, h) => <span key={h} className="tabular text-center text-[10px] text-faint">{h % 3 === 0 ? h : ''}</span>)}
          {heat.map((row, d) => [
            <span key={`l${d}`} className="text-xs text-muted">{DAYS[d]}</span>,
            ...row.map((v, h) => (
              <span key={`${d}-${h}`} title={t('{{v0}} {{v1}}–{{v2}} Uhr: {{v3}}', { v0: DAYS[d], v1: h, v2: h + 1, v3: v ? fmtDur(v) : t('nichts') })}
                className="aspect-square rounded-[3px] bg-surface-2"
                style={v ? { background: `color-mix(in oklab, var(--accent) ${15 + (v / max) * 85}%, var(--surface-2))` } : undefined} />
            )),
          ])}
        </div>
      </div>
      <p className="mt-2 text-xs text-faint">{t('Nach Startzeit der Sitzung, Zeitzone Schweiz')}</p>
    </div>
  )
}

export default function ListeningPage() {
  const admin = useAdmin()
  const [params, setParams] = useSearchParams()
  const period = (['week', 'month', 'year'].includes(params.get('p') ?? '') ? params.get('p') : 'week') as Period
  const offset = Math.min(0, Number(params.get('o')) || 0)
  const user = params.get('u') ?? ''
  const [more, setMore] = useState(50)
  const set = (p: Period, o: number, u: string) => { setMore(50); setParams(Object.fromEntries(Object.entries({ p, o: o ? String(o) : '', u }).filter(([, v]) => v)), { replace: true }) }
  const { data: d, error, reload } = useData<Listening>(`/listening?period=${period}&offset=${offset}&user=${encodeURIComponent(user)}`)
  const sub = admin ? t('Wer wann was gehört hat, aus den Hörsitzungen in Audiobookshelf.') : t('Was du wann gehört hast, aus deinen Hörsitzungen in Audiobookshelf.')
  const controls = (
    <div className="flex flex-wrap items-center gap-2">
      <div className="flex gap-1.5" role="group" aria-label={t('Zeitraum')}>{PERIODS.map(([p, l]) => <Chip key={p} active={period === p} onClick={() => set(p, 0, user)}>{l}</Chip>)}</div>
      <div className="flex items-center gap-1">
        <button type="button" onClick={() => set(period, offset - 1, user)} aria-label={t('Früherer Zeitraum')} className="flex h-9 w-9 items-center justify-center rounded-lg border border-line bg-surface text-muted hover:text-text"><ChevronLeft size={17} aria-hidden /></button>
        <span className="tabular min-w-40 px-2 text-center text-sm font-semibold">{d?.range.label ?? '…'}</span>
        <button type="button" onClick={() => set(period, offset + 1, user)} disabled={offset >= 0} aria-label={t('Späterer Zeitraum')} className="flex h-9 w-9 items-center justify-center rounded-lg border border-line bg-surface text-muted hover:text-text disabled:opacity-40"><ChevronRight size={17} aria-hidden /></button>
        {offset < 0 && <Button onClick={() => set(period, 0, user)}>{t('Aktuell')}</Button>}
      </div>
      {admin && <label className="flex items-center gap-2 text-sm text-muted">{t('Konto')}
        <select value={user} onChange={e => set(period, offset, e.target.value)} className="h-9 rounded-lg border border-line bg-bg px-2 text-sm text-text">
          <option value="">{t('Alle Konten')}</option>
          {(d?.users ?? []).slice().sort((a, b) => a.name.localeCompare(b.name, 'de')).map(u => <option key={u.abs_user_id} value={u.abs_user_id}>{u.name}</option>)}
        </select>
      </label>}
    </div>
  )
  if (error && !d) return <Page title={t('Hörverlauf')} subtitle={sub}>{controls}<div className="mt-4"><ErrorBox message={error} onRetry={reload} /></div></Page>
  if (!d) return <Page title={t('Hörverlauf')} subtitle={sub}>{controls}<div className="skeleton mt-4 h-64 rounded-2xl" /></Page>
  const s = d.summary
  const one = d.users.find(u => u.abs_user_id === d.user)
  const label = PREV[period]
  const empty = !s.seconds
  return (
    <Page title={t('Hörverlauf')} subtitle={sub}
      actions={<Link to="/rueckblick" className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-accent-strong px-4 text-sm font-semibold text-on-accent"><Sparkles size={15} aria-hidden />{t('Jahresrückblick')}</Link>}>
      {controls}
      {!d.users.length && <div className="mt-6"><Empty title={t('Noch keine Hörsitzungen')} text={admin
        ? t('Die Aufgabe «Hörverlauf abgleichen» liest sie alle 15 Minuten aus Audiobookshelf. Unter Aufgaben lässt sie sich sofort starten.')
        : t('Hier erscheint, was du in Audiobookshelf hörst. Dafür muss dein Konto mit Audiobookshelf verknüpft sein; das richtet ein Administrator ein.')} /></div>}

      <Section title={!admin ? t('Dein Rückblick') : one ? t('Rückblick {{v0}}', { v0: one.name }) : t('Rückblick alle Konten')} hint={d.range.label}>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Tile label={t('Hörzeit')} value={fmtBig(s.seconds)} sub={trend(s.seconds, s.prev_seconds, label)} />
          <Tile label={t('Bücher')} value={fmtNum(s.books)} sub={t('{{v0}} beendet, {{v1}} Sitzungen', { v0: fmtNum(s.finished), v1: fmtNum(s.sessions) })} />
          <Tile label={t('Tage mit Hören')} value={`${s.days}/${d.range.days}`} sub={t(s.streak === 1 ? 'Längste Folge im Zeitraum: 1 Tag{{v2}}' : 'Längste Folge im Zeitraum: {{v0}} Tage{{v2}}', { v0: s.streak, v2: s.current_streak != null && one ? t(', aktuell {{v0}}', { v0: s.current_streak }) : '' })} />
          <Tile label={t('Pro Tag mit Hören')} value={s.days ? fmtBig(s.seconds / s.days) : '–'} sub={one ? t('Zuletzt gehört: {{v0}}', { v0: one.last ? fmtWhen(one.last) : t('nie') }) : t('{{v0}} von {{v1}} Konten haben gehört', { v0: s.listeners, v1: d.users.length })} />
        </div>
        {!empty && (
          <ul className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {d.books[0] && <li className="rounded-xl border border-line bg-surface"><p className="px-4 pt-3 text-sm text-muted">{t('Am meisten gehört')}</p><ul><BookLine b={d.books[0]} right={fmtDur(d.books[0].seconds)} /></ul></li>}
            {s.longest && <li className="rounded-xl border border-line bg-surface"><p className="px-4 pt-3 text-sm text-muted">{t('Längste Sitzung')}</p><ul><BookLine b={s.longest} sub={fmtWhen(s.longest.started)} right={fmtDur(s.longest.seconds)} /></ul></li>}
            <li className="rounded-xl border border-line bg-surface p-4 text-sm">
              <p className="text-muted">{t('Gewohnheiten')}</p>
              <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
                {d.authors[0] && <><dt className="text-faint">{t('Autor')}</dt><dd className="truncate">{d.authors[0].name} ({fmtDur(d.authors[0].seconds)})</dd></>}
                {s.best_slot && <><dt className="text-faint">{t('Stärkster Monat')}</dt><dd>{MONTHS[Number(s.best_slot.key.slice(5)) - 1]} ({fmtDur(s.best_slot.seconds)})</dd></>}
                {s.best_day && <><dt className="text-faint">{t('Stärkster Tag')}</dt><dd>{fmtDay(s.best_day.day)} ({fmtDur(s.best_day.seconds)})</dd></>}
                {s.weekday && <><dt className="text-faint">{t('Meist am')}</dt><dd>{s.weekday}{t(', um')}{' '}{s.hour} {t('Uhr')}</dd></>}
                <dt className="text-faint">{t('Sprache')}</dt><dd>{['en', 'de'].filter(l => s.lang[l]).map(l => `${l === 'en' ? t('Englisch') : t('Deutsch')} ${Math.round((s.lang[l] / s.seconds) * 100)} %`).join(', ') || '–'}</dd>
              </dl>
            </li>
          </ul>
        )}
        {s.corrected > 0 && <p className="mt-2 text-xs text-faint">{s.corrected} {s.corrected === 1 ? t('Sitzung') : t('Sitzungen')} {t('gekürzt: Audiobookshelf meldete mehr Hörzeit, als zwischen Start und Ende verging, als das Buch lang ist oder als die Position weiterlief.')}</p>}
      </Section>

      {!d.user && d.users.length > 0 && (
        <Section title={t('Nach Konto')} hint={t('Auf einen Namen tippen zeigt nur dieses Konto')}>
          <div className="overflow-x-auto rounded-xl border border-line bg-surface">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-faint"><tr>
                <th className="px-4 py-3 font-semibold">{t('Konto')}</th><th className="px-4 py-3 text-right font-semibold">{t('Hörzeit')}</th><th className="px-4 py-3 text-right font-semibold">{label}</th>
                <th className="px-4 py-3 text-right font-semibold">{t('Bücher')}</th><th className="px-4 py-3 text-right font-semibold">{t('Beendet')}</th><th className="px-4 py-3 text-right font-semibold">{t('Tage')}</th><th className="px-4 py-3 font-semibold">{t('Zuletzt gehört')}</th>
              </tr></thead>
              <tbody>{d.users.map(u => (
                <tr key={u.abs_user_id} className={`border-t border-line ${u.seconds ? '' : 'text-muted'}`}>
                  <td className="px-4 py-2.5"><button type="button" onClick={() => set(period, offset, u.abs_user_id)} className="font-medium hover:text-accent hover:underline">{u.name}</button>
                    {u.abw_id == null && <span className="ml-2 text-xs text-faint" title={t('Dieses Audiobookshelf-Konto ist nicht mit einem ABW-Benutzer verknüpft')}>{t('nur Audiobookshelf')}</span>}</td>
                  <td className="tabular whitespace-nowrap px-4 py-2.5 text-right">{u.seconds ? fmtDur(u.seconds) : '–'}</td>
                  <td className="tabular whitespace-nowrap px-4 py-2.5 text-right text-muted">{u.prev_seconds ? fmtDur(u.prev_seconds) : '–'}</td>
                  <td className="tabular px-4 py-2.5 text-right">{u.books || '–'}</td><td className="tabular px-4 py-2.5 text-right">{u.finished || '–'}</td><td className="tabular px-4 py-2.5 text-right">{u.days || '–'}</td>
                  <td className="tabular whitespace-nowrap px-4 py-2.5 text-muted">{u.last ? fmtWhen(u.last) : t('nie')}</td>
                </tr>))}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      {!empty && <>
        <Section title={t('Hörzeit im Verlauf')} hint={period === 'year' ? t('Pro Monat') : t('Pro Tag')}><Timeline rows={d.timeline} period={period} /></Section>
        <div className="grid grid-cols-1 gap-x-6 lg:grid-cols-2">
          <Section title={t('Am meisten gehört')} hint={d.books.length < s.books ? t('Top {{v0}} von {{v1}} Büchern', { v0: d.books.length, v1: s.books }) : t('{{v0}} Büchern', { v0: s.books })}>
            <ul className="rounded-xl border border-line bg-surface">
              {d.books.slice(0, 10).map(b => (
                <BookLine key={b.item_id} b={b}
                  sub={d.user ? (b.finished ? 'beendet' : b.progress ? t('{{v0}} gehört', { v0: pct(b.progress) }) : undefined) : b.listeners.join(', ')}
                  right={<>{fmtDur(b.seconds)}<span className="block text-xs text-faint">{b.sessions} {b.sessions === 1 ? t('Sitzung') : t('Sitzungen')}</span></>} />
              ))}
            </ul>
          </Section>
          <Section title={t('Beendet')} hint={t('{{v0}} im Zeitraum', { v0: d.finished.length })}>
            {d.finished.length
              ? <ul className="max-h-[42rem] overflow-y-auto rounded-xl border border-line bg-surface">{d.finished.map(b => <BookLine key={b.item_id + b.name} b={b} sub={d.user ? undefined : b.name} right={fmtDay(b.finished_at.slice(0, 10))} />)}</ul>
              : <p className="rounded-xl border border-line bg-surface p-4 text-sm text-muted">{t('In diesem Zeitraum wurde kein Buch beendet.')}</p>}
          </Section>
          <Section title={t('Wann gehört wird')}><Heat heat={d.heat} /></Section>
          <Section title={t('Autoren')}><Bars unit="h" rows={d.authors.map(a => ({ label: a.name, value: hours(a.seconds) }))} /></Section>
          <Section title={t('Geräte und Apps')}><Bars unit="h" rows={d.devices.map(a => ({ label: <span title={a.name}>{a.name}</span>, value: hours(a.seconds) }))} /></Section>
          {d.user && (
            <Section title={t('Liegengeblieben')} hint={t('Angefangen, seit 30 Tagen nicht weitergehört')}>
              {d.stale.length
                ? <ul className="max-h-[30rem] overflow-y-auto rounded-xl border border-line bg-surface">{d.stale.map(b => <BookLine key={b.item_id} b={b} sub={`zuletzt ${fmtDay(b.last.slice(0, 10))}`} right={pct(b.progress)} />)}</ul>
                : <p className="rounded-xl border border-line bg-surface p-4 text-sm text-muted">{t('Nichts liegengeblieben.')}</p>}
            </Section>
          )}
        </div>
      </>}

      {!empty && (
        <Section title={t('Alle Sitzungen')} hint={t('{{v0}} im Zeitraum, neueste zuerst', { v0: fmtNum(d.history_total) })}>
          <div className="overflow-x-auto rounded-xl border border-line bg-surface">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-faint"><tr>
                <th className="px-4 py-3 font-semibold">{t('Beginn')}</th>{!d.user && <th className="px-4 py-3 font-semibold">{t('Konto')}</th>}<th className="px-4 py-3 font-semibold">{t('Buch')}</th>
                <th className="px-4 py-3 text-right font-semibold">{t('Gehört')}</th><th className="px-4 py-3 text-right font-semibold">{t('Position')}</th><th className="px-4 py-3 font-semibold">{t('Gerät')}</th>
              </tr></thead>
              <tbody>{d.history.slice(0, more).map(h => (
                <tr key={h.id} className="border-t border-line">
                  <td className="tabular whitespace-nowrap px-4 py-2.5">{fmtWhen(h.started)}</td>
                  {!d.user && <td className="whitespace-nowrap px-4 py-2.5">{h.name}</td>}
                  <td className="max-w-[18rem] px-4 py-2.5"><p className="truncate font-medium">{h.title}</p><p className="truncate text-xs text-muted">{h.author}</p></td>
                  <td className="tabular whitespace-nowrap px-4 py-2.5 text-right" title={h.raw_seconds && h.raw_seconds > h.seconds + 60 ? t('Audiobookshelf meldet {{v0}}', { v0: fmtDur(h.raw_seconds) }) : undefined}>
                    {fmtDur(h.seconds)}{h.raw_seconds && h.raw_seconds > h.seconds + 60 ? ' *' : ''}</td>
                  <td className="tabular whitespace-nowrap px-4 py-2.5 text-right text-muted">{h.duration && h.pos_end != null ? t('{{v0}} → {{v1}}', { v0: pct((h.pos_start ?? 0) / h.duration), v1: pct(h.pos_end / h.duration) }) : '–'}</td>
                  <td className="max-w-[14rem] truncate px-4 py-2.5 text-muted" title={h.device}>{h.device || '–'}</td>
                </tr>))}
              </tbody>
            </table>
            {(more < d.history.length || d.history_total > d.history.length) && (
              <div className="flex flex-wrap items-center gap-3 border-t border-line px-4 py-2.5 text-xs text-faint">
                {more < d.history.length && <Button onClick={() => setMore(more + 100)}>{t('Mehr zeigen')}</Button>}
                {d.history_total > d.history.length && <span>{t('Gezeigt werden die neuesten')}{' '}{d.history.length}{t('. Ältere über einen kürzeren Zeitraum ansehen.')}</span>}
              </div>
            )}
          </div>
          <p className="mt-2 text-xs text-faint">{t('* gekürzt, der Wert von Audiobookshelf steht beim Darüberfahren')}</p>
        </Section>
      )}
    </Page>
  )
}
