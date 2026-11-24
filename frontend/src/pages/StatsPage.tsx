import { Fragment, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { Link } from 'react-router-dom'
import { fmtNum, useData, type Flag } from '../api'
import { Chip, ErrorBox, LangTag, Page, Section, STATUS } from '../ui'
import { StarText } from '../Stars'
import { locale, t } from '../i18n'

interface Tot { books: number; hours: number; gb: number; finished: number; finished_hours: number; epub: number; series: number; missing: number; upcoming: number }
interface Stats {
  totals: { en: Tot; de: Tot }; series_total: number; singles: number; without_audible: number
  status: { key: Flag; n: number }[]; added: { month: string; lang: string; n: number }[]; files: { single: number; multi: number }
  top_authors: { name: string; hours: number }[]; top_narrators: { name: string; hours: number }[]
  longest_series: { id: number; name: string; hours: number; editions?: number }[]
  activity: { d1: Act; d7: Act }
  wished: Wished[] | null
}
interface Wished {
  name: string; books: number; hours: number; series: number
  items: { kind: 'series' | 'single'; id: number | null; key: string; name: string; books: number }[]
}
interface Act {
  searched: number; started: number; placed: number; placed_gb: number; failed: number; rejected: number
  added: number; converted: number; chapters: number; ebooks: number; sources: { source: string; name?: string; started: number; placed: number }[]
}


/** Aktivität der letzten 24 Stunden und 7 Tage nebeneinander, damit kein Umschalten nötig ist. */
function Activity({ d1, d7 }: { d1: Act; d7: Act }) {
  const gb = (a: Act) => (a.placed ? t('{{v0}} ({{v1}} GB)', { v0: fmtNum(a.placed), v1: a.placed_gb.toLocaleString(locale()) }) : '0')
  const rows: [string, string, string][] = [
    [t('Bände gesucht'), fmtNum(d1.searched), fmtNum(d7.searched)],
    [t('Downloads gestartet'), fmtNum(d1.started), fmtNum(d7.started)],
    [t('Abgelegt'), gb(d1), gb(d7)],
    [t('Fehlgeschlagen'), fmtNum(d1.failed), fmtNum(d7.failed)],
    [t('Verworfen'), fmtNum(d1.rejected), fmtNum(d7.rejected)],
    [t('Neu im Bestand'), fmtNum(d1.added), fmtNum(d7.added)],
    [t('In M4B umgewandelt'), fmtNum(d1.converted), fmtNum(d7.converted)],
    [t('Kapitel ergänzt'), fmtNum(d1.chapters), fmtNum(d7.chapters)],
    [t('EPUBs abgelegt'), fmtNum(d1.ebooks), fmtNum(d7.ebooks)],
  ]
  const names = [...new Set([...d7.sources, ...d1.sources].map(x => x.source))]
  const label = (n: string) => [...d7.sources, ...d1.sources].find(x => x.source === n)?.name ?? n
  const cell = (a: Act, n: string) => { const x = a.sources.find(y => y.source === n); return x ? t('{{a}} von {{b}}', { a: fmtNum(x.placed), b: fmtNum(x.started) }) : '0' }
  const th = 'px-4 py-3 text-right font-semibold'
  const td = 'tabular whitespace-nowrap px-4 py-2.5 text-right'
  return (
    <div className="grid grid-cols-1 gap-x-6 gap-y-3 lg:grid-cols-2">
      <div className="overflow-x-auto rounded-xl border border-line bg-surface">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase tracking-wide text-faint"><tr><th className="px-4 py-3 font-semibold">{t('Vorgang')}</th><th className={th}>{t('24 Stunden')}</th><th className={th}>{t('7 Tage')}</th></tr></thead>
          <tbody>{rows.map(r => <tr key={r[0]} className="border-t border-line"><td className="px-4 py-2.5">{r[0]}</td><td className={td}>{r[1]}</td><td className={td}>{r[2]}</td></tr>)}</tbody>
        </table>
      </div>
      <div className="overflow-x-auto self-start rounded-xl border border-line bg-surface">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase tracking-wide text-faint"><tr><th className="px-4 py-3 font-semibold">{t('Quelle')}</th><th className={th}>{t('24 Stunden')}</th><th className={th}>{t('7 Tage')}</th></tr></thead>
          <tbody>
            {names.map(n => <tr key={n} className="border-t border-line"><td className="px-4 py-2.5">{label(n)}</td><td className={td}>{cell(d1, n)}</td><td className={td}>{cell(d7, n)}</td></tr>)}
            {!names.length && <tr className="border-t border-line"><td colSpan={3} className="px-4 py-2.5 text-muted">{t('Keine Downloads in den letzten 7 Tagen')}</td></tr>}
          </tbody>
        </table>
        <p className="border-t border-line px-4 py-2.5 text-xs text-faint">{t('Abgelegte von gestarteten Downloads je Quelle')}</p>
      </div>
    </div>
  )
}

/** Wer welchen Teil des Bestands gewünscht hat; ein Name klappt seine Serien und Einzelbuch-Karten auf. */
function WishedTable({ rows }: { rows: Wished[] }) {
  const [open, setOpen] = useState<string | null>(null)
  const td = 'tabular px-2 py-2.5 text-right sm:px-4'
  return (
    <div className="overflow-x-auto rounded-xl border border-line bg-surface">
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase tracking-wide text-faint"><tr><th className="px-3 py-3 font-semibold sm:px-4">{t('Benutzer')}</th><th className="px-2 py-3 text-right font-semibold sm:px-4">{t('Serien')}</th><th className="px-2 py-3 text-right font-semibold sm:px-4">{t('Bücher')}</th><th className="px-2 py-3 text-right font-semibold sm:px-4">{t('Stunden')}</th></tr></thead>
        <tbody>
          {rows.map(w => (
            <Fragment key={w.name}>
              <tr className="border-t border-line">
                <td className="px-1 py-1 sm:px-2">
                  <button type="button" onClick={() => setOpen(open === w.name ? null : w.name)} aria-expanded={open === w.name}
                    className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-2 font-medium hover:text-accent">
                    <ChevronRight size={15} aria-hidden className={`text-faint transition-transform ${open === w.name ? 'rotate-90' : ''}`} />{w.name}
                  </button>
                </td>
                <td className={td}>{fmtNum(w.series)}</td><td className={td}>{fmtNum(w.books)}</td><td className={td}>{fmtNum(w.hours)}</td>
              </tr>
              {open === w.name && (
                <tr className="bg-surface-2/40"><td colSpan={4} className="px-4 pb-3 pt-1">
                  <ul className="divide-y divide-line">
                    {w.items.map(i => (
                      <li key={`${i.kind}-${i.key}`} className="flex items-center gap-3 py-2">
                        <Link to={i.kind === 'series' ? `/serie/${i.id}` : `/einzelbuecher/${i.key}`} className="min-w-0 flex-1 truncate hover:text-accent hover:underline">{i.name}</Link>
                        <span className="tabular shrink-0 text-xs text-muted">{fmtNum(i.books)} {i.books === 1 ? t('Buch') : t('Bücher')}</span>
                      </li>
                    ))}
                  </ul>
                </td></tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <p className="text-sm text-muted">{label}</p>
      <p className="tabular mt-1 font-[family-name:var(--font-display)] text-3xl font-bold">{value}</p>
      {sub && <p className="tabular mt-1 text-xs text-faint">{sub}</p>}
    </div>
  )
}

/** Waagrechte Balken mit Wert am Ende: lesbar ohne Legende und ohne Farbcode. */
export function Bars({ rows, unit, color = 'var(--accent)' }: { rows: { label: React.ReactNode; value: number; color?: string }[]; unit: string; color?: string }) {
  const max = Math.max(1, ...rows.map(r => r.value))
  return (
    <ul className="space-y-2.5 rounded-xl border border-line bg-surface p-4">
      {rows.map((r, i) => (
        <li key={i} className="grid grid-cols-[minmax(0,6.5rem)_1fr_auto] sm:grid-cols-[minmax(0,11rem)_1fr_auto] items-center gap-3 text-sm">
          <span className="truncate">{r.label}</span>
          <span className="h-2.5 overflow-hidden rounded-full bg-surface-2"><span className="block h-full rounded-full" style={{ width: `${(r.value / max) * 100}%`, background: r.color ?? color }} /></span>
          <span className="tabular w-16 text-right text-muted sm:w-20">{fmtNum(r.value)} {unit}</span>
        </li>
      ))}
    </ul>
  )
}

interface Ranked { name: string; avg: number; count: number; id: number | null }
interface RStats { rated: number; skipped: number; open: number | null; average: number | null; distribution: Record<string, number>; authors: Ranked[]; narrators: Ranked[]; series: Ranked[] }

/** Beste Autoren, Sprecher oder Serien nach Sternen (mindestens zwei bewertete Bücher). */
function RankList({ rows, series }: { rows: Ranked[]; series?: boolean }) {
  if (!rows.length) return <p className="rounded-xl border border-line bg-surface p-4 text-sm text-muted">{t('Noch zu wenig bewertet: es braucht mindestens zwei bewertete Bücher je Eintrag.')}</p>
  return (
    <ol className="rounded-xl border border-line bg-surface">
      {rows.map((r, i) => (
        <li key={r.name} className="flex items-center gap-3 border-t border-line px-4 py-2 text-sm first:border-t-0">
          <span className="tabular w-5 text-right text-faint">{i + 1}</span>
          <span className="min-w-0 flex-1 truncate">{series && r.id ? <Link to={`/serie/${r.id}`} className="hover:text-accent hover:underline">{r.name}</Link> : r.name}</span>
          <StarText n={r.avg} />
          <span className="tabular w-20 text-right text-xs text-muted">{r.avg.toLocaleString(locale())} · {r.count} {r.count === 1 ? t('Buch') : t('Bücher')}</span>
        </li>
      ))}
    </ol>
  )
}

/** Bewertungen: eigene oder die aller Benutzer. */
function RatingStats() {
  const [scope, setScope] = useState<'me' | 'all'>('me')
  const { data: r, error, reload } = useData<RStats>(`/ratings/stats?scope=${scope}`)
  return (
    <>
      <div className="mt-8 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">{t('Bewertungen')}</h2>
        <div className="flex gap-2">
          <Chip active={scope === 'me'} onClick={() => setScope('me')}>{t('Meine')}</Chip>
          <Chip active={scope === 'all'} onClick={() => setScope('all')}>{t('Alle Benutzer')}</Chip>
        </div>
      </div>
      {error && !r ? <div className="mt-3"><ErrorBox message={error} onRetry={reload} /></div> : !r ? <div className="skeleton mt-3 h-40 rounded-2xl" /> : !r.rated ? (
        <p className="mt-3 rounded-xl border border-line bg-surface p-4 text-sm text-muted">{t('Noch keine Bewertungen.')}{' '}<Link to="/bewertungen" className="text-accent hover:underline">{t('Jetzt bewerten')}</Link></p>
      ) : (
        <>
          <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Tile label={t('Bewertet')} value={fmtNum(r.rated)} sub={r.skipped ? `${fmtNum(r.skipped)} bewusst nicht bewertet` : undefined} />
            <Tile label={t('Schnitt')} value={`${(r.average ?? 0).toLocaleString(locale())} ★`} />
            {r.open != null && <Tile label={t('Noch offen')} value={fmtNum(r.open)} sub={t('gehört, aber nicht bewertet')} />}
          </div>
          <div className="grid grid-cols-1 gap-x-6 lg:grid-cols-2">
            <Section title={t('Verteilung der Sterne')}>
              <Bars unit="" color="var(--warn)" rows={[5, 4, 3, 2, 1].map(n => ({ label: `${'★'.repeat(n)}`, value: r.distribution[String(n)] ?? 0 }))} />
            </Section>
            <Section title={t('Beste Serien')}><RankList rows={r.series} series /></Section>
            <Section title={t('Beste Autoren')}><RankList rows={r.authors} /></Section>
            <Section title={t('Beste Sprecher')}><RankList rows={r.narrators} /></Section>
          </div>
        </>
      )}
    </>
  )
}

export default function StatsPage() {
  const { data: s, error, reload } = useData<Stats>('/stats')
  if (error && !s) return <Page title={t('Statistik')}><ErrorBox message={error} onRetry={reload} /></Page>
  if (!s) return <Page title={t('Statistik')}><div className="skeleton h-64 rounded-2xl" /></Page>
  const { en, de } = s.totals
  const months = [...new Set(s.added.map(a => a.month))].slice(-18)
  const perMonth = months.map(m => ({ m, en: s.added.find(a => a.month === m && a.lang === 'en')?.n ?? 0, de: s.added.find(a => a.month === m && a.lang === 'de')?.n ?? 0 }))
  const maxMonth = Math.max(1, ...perMonth.map(p => p.en + p.de))
  return (
    <Page title={t('Statistik')} subtitle={t('Alle Zahlen stammen aus Audiobookshelf und dem Audible-Abgleich')}>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Tile label={t('Serien')} value={fmtNum(s.series_total)} sub={t('{{v0}} englisch, {{v1}} deutsch', { v0: en.series, v1: de.series })} />
        <Tile label={t('Bücher')} value={fmtNum(en.books + de.books)} sub={t('{{v0}} englisch, {{v1}} deutsch, davon {{v2}} ohne Serie', { v0: fmtNum(en.books), v1: fmtNum(de.books), v2: fmtNum(s.singles) })} />
        <Tile label={t('Laufzeit')} value={`${fmtNum(en.hours + de.hours)} h`} sub={t('{{v0}} Tage am Stück', { v0: fmtNum(Math.round((en.hours + de.hours) / 24)) })} />
        <Tile label={t('Speicher')} value={`${fmtNum(en.gb + de.gb)} GB`} sub={t('{{v0}} Bücher als eine Datei, {{v1}} mit mehreren', { v0: fmtNum(s.files.single), v1: fmtNum(s.files.multi) })} />
        <Tile label={t('Gehört')} value={fmtNum(en.finished + de.finished)} sub={t('{{v0}} Stunden beendet', { v0: fmtNum(en.finished_hours + de.finished_hours) })} />
        <Tile label={t('Fehlende Bände')} value={fmtNum(en.missing + de.missing)} sub={t('{{v0}} englisch, {{v1}} deutsch (überwachte Fassungen)', { v0: en.missing, v1: de.missing })} />
        <Tile label={t('Angekündigt')} value={fmtNum(en.upcoming + de.upcoming)} sub={t('{{v0}} englisch, {{v1}} deutsch', { v0: en.upcoming, v1: de.upcoming })} />
        <Tile label={t('Ohne Audible-Abgleich')} value={fmtNum(s.without_audible)} sub={t('Serien, die noch nicht geprüft sind (Einzelbücher und Titel, die Audible nicht führt, zählen nicht)')} />
      </div>
      <Section title={t('Aktivität')} hint={t('Letzte 24 Stunden und letzte 7 Tage')}><Activity d1={s.activity.d1} d7={s.activity.d7} /></Section>
      <div className="grid grid-cols-1 gap-x-6 lg:grid-cols-2">
        <Section title={t('Serien nach Status')}>
          <Bars unit="" rows={s.status.map(x => ({ label: STATUS[x.key].label, value: x.n, color: STATUS[x.key].color }))} />
        </Section>
        <Section title={t('Neu im Bestand pro Monat')} hint={t('Letzte 18 Monate')}>
          <div className="overflow-x-auto rounded-xl border border-line bg-surface p-4">
            <div className="min-w-[540px]">
            <div className="flex h-44 items-end gap-1.5" role="img" aria-label={t('Neue Bücher pro Monat: {{v0}}', { v0: perMonth.map(p => `${p.m} ${p.en + p.de}`).join(', ') })}>
              {perMonth.map(p => (
                <div key={p.m} className="flex h-full flex-1 flex-col justify-end gap-px" title={t('{{v0}}: {{v1}} englisch, {{v2}} deutsch', { v0: p.m, v1: p.en, v2: p.de })}>
                  <span className="tabular mb-1 text-center text-[10px] text-faint">{p.en + p.de}</span>
                  <span className="rounded-t-sm" style={{ height: `${(p.de / maxMonth) * 82}%`, background: 'var(--info)' }} />
                  <span className={p.de ? '' : 'rounded-t-sm'} style={{ height: `${(p.en / maxMonth) * 82}%`, background: 'var(--accent)' }} />
                </div>
              ))}
            </div>
            <div className="mt-2 flex gap-1.5 text-[10px] text-faint">{perMonth.map(p => <span key={p.m} className="tabular flex-1 text-center">{p.m.slice(5)}/{p.m.slice(2, 4)}</span>)}</div>
            </div>
            <p className="mt-3 flex gap-4 text-xs text-muted">
              <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: 'var(--accent)' }} />{t('Englisch')}</span>
              <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: 'var(--info)' }} />{t('Deutsch')}</span>
            </p>
          </div>
        </Section>
        <Section title={t('Autoren mit der längsten Laufzeit')} hint={t('Serien mit mehreren Fassungen zählen mit deren mittlerer Laufzeit')}><Bars unit="h" rows={s.top_authors.map(a => ({ label: a.name, value: a.hours }))} /></Section>
        <Section title={t('Sprecher mit der längsten Laufzeit')}><Bars unit="h" rows={s.top_narrators.map(a => ({ label: a.name, value: a.hours }))} /></Section>
        <Section title={t('Längste Serien im Bestand')} hint={t('Mittel der Fassungen im Bestand')}>
          <Bars unit="h" rows={s.longest_series.map(x => ({ label: <Link to={`/serie/${x.id}`} className="hover:text-accent hover:underline" title={x.editions && x.editions > 1 ? t('Mittel aus {{v0}} Fassungen', { v0: x.editions }) : undefined}>{x.name}</Link>, value: x.hours }))} />
        </Section>
        <Section title={t('Sprachen im Vergleich')}>
          <div className="overflow-x-auto rounded-xl border border-line bg-surface">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-faint"><tr><th className="px-4 py-3 font-semibold">{t('Sprache')}</th><th className="px-4 py-3 text-right font-semibold">{t('Serien')}</th><th className="px-4 py-3 text-right font-semibold">{t('Bücher')}</th><th className="px-4 py-3 text-right font-semibold">{t('Stunden')}</th><th className="px-4 py-3 text-right font-semibold">{t('Gehört')}</th><th className="px-4 py-3 text-right font-semibold">{t('Mit EPUB')}</th></tr></thead>
              <tbody>
                {([['en', en], ['de', de]] as const).map(([l, tx]) => (
                  <tr key={l} className="border-t border-line"><td className="px-4 py-2.5"><LangTag lang={l} /></td>
                    <td className="tabular px-4 py-2.5 text-right">{fmtNum(tx.series)}</td><td className="tabular px-4 py-2.5 text-right">{fmtNum(tx.books)}</td>
                    <td className="tabular px-4 py-2.5 text-right">{fmtNum(tx.hours)}</td><td className="tabular px-4 py-2.5 text-right">{fmtNum(tx.finished)}</td><td className="tabular px-4 py-2.5 text-right">{fmtNum(tx.epub)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      </div>
      {s.wished && (
        <Section title={t('Gewünscht von')} hint={t('Bestand, den Benutzer gewünscht haben (Buch oder Serie, die ganze Serie zählt); entspricht dem Tag «Gewünscht von» in Audiobookshelf. Name antippen zeigt die Serien.')}>
          {s.wished.length ? <WishedTable rows={s.wished} /> : <p className="rounded-xl border border-line bg-surface p-4 text-sm text-muted">{t('Noch keine Wünsche von Benutzern im Bestand.')}</p>}
        </Section>
      )}
      <RatingStats />
    </Page>
  )
}
