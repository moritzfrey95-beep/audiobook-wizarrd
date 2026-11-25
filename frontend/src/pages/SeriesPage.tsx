import { useEffect, useMemo, useState } from 'react'
import { Search, X } from 'lucide-react'
import { useData, type Flag, type LibraryTags, type Series, type SingleGroup } from '../api'
import { SeriesCard, SingleCard } from '../SeriesCard'
import { Chip, Empty, ErrorBox, GRID, Page, SkeletonGrid, STATUS } from '../ui'
import { useAdmin, useLangs } from '../session'
import { Link } from 'react-router-dom'
import type { SetupState } from '../SetupWizard'
import { t } from '../i18n'

/** Eine Karte der Serienansicht: echte Serie oder «Autor – Einzelbücher». */
type Item = { kind: 'series'; s: Series; key: string } | { kind: 'single'; g: SingleGroup; key: string }
type Common = Pick<Series, 'name' | 'author' | 'flags' | 'added_at' | 'missing' | 'next_release' | 'hours'>
const common = (it: Item): Common => it.kind === 'series' ? it.s : it.g
const NONE = { genres: [] as string[], tags: [] as string[] }

const FILTERS: Flag[] = ['incomplete', 'upcoming', 'searching', 'downloading', 'review', 'failed', 'complete', 'ended', 'unknown', 'single', 'absent']
const SORTS = { name: t('Name'), added: t('Zuletzt hinzugefügt'), missing: t('Meiste fehlende Bände'), next: t('Nächster Erscheinungstermin'), hours: t('Längste Laufzeit') }
type Sort = keyof typeof SORTS

function stored<T>(key: string, fallback: T): T {
  try { const v = sessionStorage.getItem(key); return v ? JSON.parse(v) : fallback } catch { return fallback }
}

export default function SeriesPage() {
  const { data, error, loading, reload } = useData<Series[]>('/series', 60000)
  const { data: singles } = useData<SingleGroup[]>('/singles', 60000)
  const [q, setQ] = useState(() => stored('abw-q', ''))
  const [flag, setFlag] = useState<Flag | null>(() => stored('abw-flag', null))
  const [lang, setLang] = useState<'all' | 'en' | 'de'>(() => stored('abw-lang', 'all'))
  const langs = useLangs()
  const [sort, setSort] = useState<Sort>(() => stored('abw-sort', 'name'))
  const { data: lt } = useData<LibraryTags>('/library/tags', 300000)
  const [genre, setGenre] = useState<string>(() => stored('abw-genre', ''))
  const [tag, setTag] = useState<string>(() => stored('abw-tag', ''))
  // Wer was gewünscht hat, nur für Administratoren
  const admin = useAdmin()
  const { data: setup } = useData<SetupState>(admin ? '/setup' : null)
  const { data: wished } = useData<{ series: Record<string, string[]>; singles: Record<string, string[]> }>(admin ? '/library/wished' : null, 300000)
  const [wisher, setWisher] = useState<string>(() => (admin ? stored('abw-wisher', '') : ''))
  const [shown, setShown] = useState(84)
  useEffect(() => {
    try {
      sessionStorage.setItem('abw-q', JSON.stringify(q)); sessionStorage.setItem('abw-flag', JSON.stringify(flag))
      sessionStorage.setItem('abw-lang', JSON.stringify(lang)); sessionStorage.setItem('abw-sort', JSON.stringify(sort))
      sessionStorage.setItem('abw-genre', JSON.stringify(genre)); sessionStorage.setItem('abw-tag', JSON.stringify(tag))
      sessionStorage.setItem('abw-wisher', JSON.stringify(wisher))
    } catch { /* Filter gelten dann nur bis zum Seitenwechsel */ }
    setShown(84)
  }, [q, flag, lang, sort, genre, tag, wisher])

  // Serien und Einzelbuch-Karten gemeinsam, nach Name wie im Backend (ohne Artikel) einsortiert
  const all = useMemo<Item[]>(() => [
    ...(data ?? []).map(s => ({ kind: 'series' as const, s, key: s.sort ?? s.name.toLowerCase() })),
    ...(singles ?? []).map(g => ({ kind: 'single' as const, g, key: g.sort })),
  ].sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0)), [data, singles])
  const byLang = useMemo(() => all.filter(it => lang === 'all' || (it.kind === 'series'
    ? it.s.editions.some(e => e.lang === lang && (e.owned > 0 || e.monitored))
    : it.g.langs.some(l => l.lang === lang))), [all, lang])
  // Genre und Tags je Karte, aus den ersten Bänden jeder Fassung
  const wishersOf = (it: Item) => (it.kind === 'series' ? wished?.series[String(it.s.id)] : wished?.singles[it.g.key]) ?? []
  const gt = (it: Item) => (it.kind === 'series' ? lt?.series[it.s.id] : lt?.singles[it.g.key]) ?? NONE
  const options = useMemo(() => {
    const g = new Map<string, number>(), tx = new Map<string, number>()
    for (const it of byLang) {
      const x = it.kind === 'series' ? lt?.series[it.s.id] : lt?.singles[it.g.key]
      for (const v of x?.genres ?? []) g.set(v, (g.get(v) ?? 0) + 1)
      for (const v of x?.tags ?? []) tx.set(v, (tx.get(v) ?? 0) + 1)
    }
    const byName = (m: Map<string, number>) => [...m].sort((a, b) => a[0].localeCompare(b[0], 'de'))
    const w = new Map<string, number>()
    for (const it of byLang) for (const v of wishersOf(it)) w.set(v, (w.get(v) ?? 0) + 1)
    return { genres: byName(g), tags: byName(tx), wishers: byName(w) }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- wishersOf hängt nur an wished
  }, [byLang, lt, wished])
  const counts = useMemo(() => {
    const c: Partial<Record<Flag, number>> = {}
    for (const it of byLang) for (const f of common(it).flags) c[f] = (c[f] ?? 0) + 1
    return c
  }, [byLang])
  const list = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const l = byLang.filter(it => {
      const s = common(it)
      const x = gt(it)
      return (!flag || s.flags.includes(flag)) && (!genre || x.genres.includes(genre)) && (!tag || x.tags.includes(tag)) && (!wisher || wishersOf(it).includes(wisher)) && (!needle || s.name.toLowerCase().includes(needle) || s.author.toLowerCase().includes(needle) ||
        (it.kind === 'series' ? it.s.editions.some(e => e.name.toLowerCase().includes(needle)) : it.g.titles.some(tx => tx.toLowerCase().includes(needle))))
    })
    const cmp: Record<Sort, (a: Common, b: Common) => number> = {
      name: () => 0,
      added: (a, b) => b.added_at.localeCompare(a.added_at),
      missing: (a, b) => b.missing - a.missing,
      next: (a, b) => (a.next_release ?? '9').localeCompare(b.next_release ?? '9'),
      hours: (a, b) => b.hours - a.hours,
    }
    return sort === 'name' ? l : [...l].sort((a, b) => cmp[sort](common(a), common(b)))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- gt hängt nur an lt
  }, [byLang, q, flag, sort, genre, tag, wisher, lt, wished])

  const reset = () => { setQ(''); setFlag(null); setLang('all'); setGenre(''); setTag(''); setWisher('') }
  return (
    <Page title={t('Serien')} subtitle={data ? t('{{v0}} von {{v1}} Serien', { v0: list.length, v1: all.length }) : t('Bestand wird geladen')}>
      {setup && !setup.auto_on && (
        <p role="status" className="mb-4 rounded-xl border border-line bg-surface px-4 py-3 text-sm">
          {t('Die Automatik ist aus: ABW sucht, lädt und legt nichts selbst ab.')}{' '}<Link to="/aufgaben" className="font-semibold text-accent underline-offset-4 hover:underline">{t('Automatik einschalten')}</Link>
        </p>
      )}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="relative min-w-56 flex-1 sm:max-w-sm">
          <label htmlFor="series-search" className="sr-only">{t('Serie, Buch oder Autor suchen')}</label>
          <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" aria-hidden />
          <input id="series-search" value={q} onChange={e => setQ(e.target.value)} placeholder={t('Serie, Buch oder Autor suchen')}
            className="h-10 w-full rounded-lg border border-line bg-surface pl-9 pr-9 text-sm placeholder:text-faint" />
          {q && (
            <button type="button" onClick={() => setQ('')} aria-label={t('Suche leeren')}
              className="absolute right-1 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded text-faint hover:text-text"><X size={15} aria-hidden /></button>
          )}
        </div>
        {langs.length > 1 && <div className="flex gap-1 rounded-lg border border-line bg-surface p-1" role="group" aria-label={t('Sprache')}>
          {(['all', ...langs] as const).map(l => (
            <button key={l} type="button" onClick={() => setLang(l)} aria-pressed={lang === l}
              className={`min-h-8 rounded-md px-3 text-sm font-medium transition-colors ${lang === l ? 'bg-surface-2 text-text' : 'text-muted hover:text-text'}`}>
              {l === 'all' ? t('Alle Sprachen') : l === 'en' ? t('Englisch') : t('Deutsch')}
            </button>
          ))}
        </div>}
        <div className="flex w-full flex-wrap gap-2 sm:w-auto sm:flex-nowrap">
          <FilterSelect id="series-genre" label={t('Genre')} value={genre} onChange={setGenre} options={options.genres} all={t('Alle Genres')} />
          <FilterSelect id="series-tag" label={t('Tag')} value={tag} onChange={setTag} options={options.tags} all={t('Alle Tags')} />
          {admin && <FilterSelect id="series-wisher" label={t('Gewünscht von')} value={wisher} onChange={setWisher} options={options.wishers} all={t('Alle Wünsche')} />}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <label htmlFor="series-sort" className="text-sm text-muted">{t('Sortierung')}</label>
          <select id="series-sort" value={sort} onChange={e => setSort(e.target.value as Sort)}
            className="h-10 rounded-lg border border-line bg-surface px-3 text-sm">
            {Object.entries(SORTS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
      </div>
      <div className="mb-5 flex gap-2 overflow-x-auto pb-1" role="group" aria-label={t('Nach Status filtern')}>
        <Chip active={!flag} onClick={() => setFlag(null)} count={byLang.length}>{t('Alle')}</Chip>
        {FILTERS.filter(f => counts[f]).map(f => {
          const Icon = STATUS[f].icon
          return (
            <Chip key={f} active={flag === f} onClick={() => setFlag(flag === f ? null : f)} count={counts[f]}>
              <Icon size={14} aria-hidden style={flag === f ? undefined : { color: STATUS[f].color }} />{STATUS[f].label}
            </Chip>
          )
        })}
      </div>
      {error && !data ? <ErrorBox message={error} onRetry={reload} />
        : loading && !data ? <SkeletonGrid />
        : list.length === 0 ? (
          <Empty title={t('Keine Serie passt zu diesen Filtern')}
            text={t('Ändere die Suche oder setze die Filter zurück, um wieder alle Serien zu sehen.')}
            action={<button type="button" onClick={reset} className="min-h-10 rounded-lg bg-accent-strong px-4 text-sm font-semibold text-on-accent">{t('Filter zurücksetzen')}</button>} />
        ) : (
          <>
            <div className={GRID}>{list.slice(0, shown).map(it => it.kind === 'series' ? <SeriesCard key={it.s.id} s={it.s} /> : <SingleCard key={`e-${it.g.key}`} g={it.g} />)}</div>
            {list.length > shown && (
              <div className="mt-6 flex justify-center">
                <button type="button" onClick={() => setShown(shown + 168)}
                  className="min-h-10 rounded-lg border border-line bg-surface px-5 text-sm font-semibold hover:border-faint">
                  {t('Weitere')}{' '}{Math.min(168, list.length - shown)} {t('Serien zeigen')}
                </button>
              </div>
            )}
          </>
        )}
    </Page>
  )
}

/** Auswahl für Genre oder Tag; ein gewählter Wert ohne Treffer bleibt sichtbar, damit er sich zurücksetzen lässt. */
function FilterSelect({ id, label, value, onChange, options, all }: {
  id: string; label: string; value: string; onChange: (v: string) => void; options: [string, number][]; all: string
}) {
  const list = value && !options.some(([v]) => v === value) ? [...options, [value, 0] as [string, number]] : options
  return (
    <div className="min-w-[9rem] flex-1 sm:min-w-0 sm:flex-none">
      <label htmlFor={id} className="sr-only">{label}</label>
      <select id={id} value={value} onChange={e => onChange(e.target.value)}
        className={`h-10 w-full rounded-lg sm:w-44 border bg-surface px-3 text-sm ${value ? 'border-accent text-text' : 'border-line text-muted'}`}>
        <option value="">{all}</option>
        {list.map(([v, n]) => <option key={v} value={v}>{v} ({n})</option>)}
      </select>
    </div>
  )
}
