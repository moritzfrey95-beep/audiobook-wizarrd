import { useContext, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2, Loader2, Search, Sparkles, Star, X } from 'lucide-react'
import { api, fmtDate, fmtRuntime, fmtSeriesSize, useData } from '../api'
import { Cover, Empty, ErrorBox, GRID, LangTag, Page, SkeletonGrid, Tabs, tell } from '../ui'
import { WatchButton } from '../Watch'
import { useLangs } from '../session'
import { DiscoverToReadButton, ToReadAsins } from '../ToRead'
import { locale, t } from '../i18n'

interface Item {
  asin: string; lang: string; title: string; author: string; narrator: string; series: string | null; sequence: string | null
  release_date: string | null; runtime_min: number | null; cover_url: string | null; rating: number | null; ratings: number | null
  in_library: boolean; series_id: number | null; watched: boolean; present?: boolean; heard?: boolean
  fit_genres?: string[]; fits?: boolean; because?: string[]; because_complete?: string[]; matches?: number
  peer?: boolean; author_known?: boolean; narrator_known?: string[]
  tags?: string[]; fit_tags?: string[]; series_books?: number | null; series_min?: number | null; explore?: boolean
}
interface AuthorSeries { series_asin: string; series_title: string; lang: string; book_count: number; series_min?: number | null; cover_url: string | null; author: string; watched: boolean }

/** Register kommen aus der Adresse: /entdecken, /entdecken/autoren, /entdecken/toplisten. */
type Tab = 'foryou' | 'authors' | 'top'

/** Schon vorhanden: im Bestand oder (bei «Für dich») als Serie oder andere Ausgabe schon in ABW. */
const known = (i: Item) => i.in_library || !!i.present

/** Was ausgeblendet wird. Standard: Vorhandenes zeigen, fertig Gehörtes ausblenden.
 *  Titel auf der eigenen Liste «To Read» standardmässig ausblenden. */
interface Hide { known: boolean; heard: boolean; toread: boolean }
type HideProps = { hide: Hide; setHide: (v: Hide) => void }
const visible = (hide: Hide, listed: Set<string>) => (i: Item) =>
  !(hide.known && known(i)) && !(hide.heard && i.heard) && !(hide.toread && listed.has(i.asin))

/** Gemeinsame Schalter für Vorschläge, Toplisten und Suche. */
function HideKnown({ hide, setHide }: HideProps) {
  const box = (key: keyof Hide, label: string) => (
    <label className="inline-flex min-h-9 items-center gap-2 text-sm text-muted">
      <input type="checkbox" checked={hide[key]} onChange={e => setHide({ ...hide, [key]: e.target.checked })} className="h-4 w-4 accent-[var(--accent)]" />
      {label}
    </label>
  )
  return <>{box('heard', t('Gehörte Titel ausblenden'))}{box('known', t('Vorhandene Titel ausblenden'))}{box('toread', t('Titel auf To Read ausblenden'))}</>
}

/** Persönliche Einstellung: beim Hinzufügen der Serie folgen, also bei neuen Bänden benachrichtigt werden. */
function FollowOnAdd() {
  const { data } = useData<{ follow_on_add: boolean }>('/me')
  const [on, setOn] = useState<boolean | null>(null)
  const value = on ?? data?.follow_on_add ?? true
  const change = async (v: boolean) => {
    setOn(v)
    try { await api('/me/prefs', { method: 'POST', body: JSON.stringify({ follow_on_add: v }) }) } catch (e) { setOn(!v); await tell((e as Error).message) }
  }
  return (
    <label className="mb-4 inline-flex min-h-9 items-center gap-2 text-sm text-muted" title={t('Folgen heisst: Du wirst bei neuen Bänden der Serie benachrichtigt')}>
      <input type="checkbox" checked={value} disabled={!data} onChange={e => change(e.target.checked)} className="h-4 w-4 accent-[var(--accent)]" />
      {t('Beim Hinzufügen der Serie folgen')}
    </label>
  )
}

const ALL_KNOWN = <Empty title={t('Alles ausgeblendet')} text={t('Schalte «Gehörte Titel ausblenden» oder «Vorhandene Titel ausblenden» aus, um die ganze Liste zu sehen.')} />

/** Karte eines Vorschlags. `asSeries`: eine Serie erscheint als Serie mit Bandzahl. */
function ItemCard({ i, asSeries = false }: { i: Item; asSeries?: boolean }) {
  const series = asSeries && !!i.series
  const audibleUrl = `https://www.audible.${i.lang === 'de' ? 'de' : 'com'}/pd/${i.asin}`
  return (
    <li className="flex flex-col overflow-hidden rounded-xl border border-line bg-surface">
      <div className="relative">
        <Cover src={i.cover_url} alt={i.title} />
        {(known(i) || i.heard) && (
          <span className="absolute left-2 top-2 inline-flex items-center gap-1 rounded-full bg-black/80 px-2.5 py-1 text-xs font-semibold" style={{ color: 'var(--ok)' }}>
            <CheckCircle2 size={13} aria-hidden />{i.heard ? t('Gehört') : i.in_library ? t('Im Bestand') : t('Schon in ABW')}
          </span>
        )}
        {i.explore && !(known(i) || i.heard) && (
          <span className="absolute left-2 top-2 inline-flex items-center gap-1 rounded-full bg-black/80 px-2.5 py-1 text-xs font-semibold" style={{ color: 'var(--info)' }}>
            <Sparkles size={13} aria-hidden />{t('Mal was anderes')}
          </span>
        )}
      </div>
      <div className="flex flex-1 flex-col gap-1.5 p-3">
        {series ? (
          <>
            {i.series_id
              ? <Link to={`/serie/${i.series_id}`} className="line-clamp-2 text-sm font-semibold leading-snug hover:text-accent">{i.series}</Link>
              : <a href={audibleUrl} target="_blank" rel="noreferrer" className="line-clamp-2 text-sm font-semibold leading-snug hover:text-accent">{i.series}</a>}
            <p className="truncate text-xs text-muted">{i.author}</p>
            {(i.series_books || i.series_min) ? <p className="tabular text-xs text-muted">{fmtSeriesSize(i.series_books, i.series_min)}</p> : null}
          </>
        ) : (
          <>
            <a href={audibleUrl} target="_blank" rel="noreferrer"
              className="line-clamp-2 text-sm font-semibold leading-snug hover:text-accent">{i.title}</a>
            <p className="truncate text-xs text-muted">{i.author}</p>
          </>
        )}
        {i.series && !series && (
          i.series_id
            ? <Link to={`/serie/${i.series_id}`} className="truncate text-xs text-accent hover:underline">{i.series}{i.sequence ? t(', Band {{v0}}', { v0: i.sequence }) : ''}</Link>
            : <p className="truncate text-xs text-muted">{i.series}{i.sequence ? t(', Band {{v0}}', { v0: i.sequence }) : ''}</p>
        )}
        {i.because && i.because.length > 0 && (
          <p className="line-clamp-2 text-xs text-muted" title={i.because.join(', ')}>{t('Ähnlich wie')}{' '}{i.because.map(b => i.because_complete?.includes(b) ? t('{{v0}} (ganz gehört)', { v0: b }) : b).join(', ')}</p>
        )}
        {(i.peer || i.author_known || (i.narrator_known && i.narrator_known.length > 0)) && (
          <p className="line-clamp-2 text-xs text-muted">
            {[i.peer && t('Hören andere hier mit ähnlichem Geschmack'), i.author_known && t('Autor kennst du'),
              i.narrator_known && i.narrator_known.length > 0 && t('Sprecher kennst du: {{v0}}', { v0: i.narrator_known.join(', ') })].filter(Boolean).join(' · ')}
          </p>
        )}
        {i.tags && i.tags.length > 0 && (
          <p className="flex flex-wrap gap-1">
            {i.tags.slice(0, 3).map(tx => (
              <span key={tx} className={`rounded-full border px-2 py-0.5 text-[11px] ${i.fit_tags?.includes(tx) ? 'border-accent text-accent' : 'border-line text-muted'}`}>{tx}</span>
            ))}
          </p>
        )}
        {!series && i.series_books != null && i.series_books > 1 && <p className="tabular text-xs text-faint">{t('Serie:')}{' '}{fmtSeriesSize(i.series_books, i.series_min)}</p>}
        {!i.because && i.fits && i.fit_genres && i.fit_genres.length > 0 && (
          <p className="truncate text-xs" style={{ color: 'var(--accent)' }}>{t('Passt zu dir:')}{' '}{i.fit_genres.slice(0, 2).join(', ')}</p>
        )}
        <p className="flex flex-wrap items-center gap-x-2 text-xs text-faint">
          {i.lang && <LangTag lang={i.lang} />}
          {i.rating && <span className="inline-flex items-center gap-1 text-muted"><Star size={12} aria-hidden style={{ color: 'var(--warn)' }} />{i.rating.toLocaleString(locale())}</span>}
          {series ? <span>{t('Serie')}</span> : <><span>{fmtRuntime(i.runtime_min)}</span><span>{fmtDate(i.release_date)}</span></>}
        </p>
        <div className="mt-auto flex flex-col gap-1.5 pt-1">
          {/* «Schon in ABW» braucht kein «Hinzufügen»; selbst Hinzugefügtes behält den Knopf zum Zurücknehmen. */}
          {!i.in_library && (!i.present || i.watched) && <WatchButton watched={i.watched} item={{ asin: i.asin, lang: i.lang, title: i.title, author: i.author, series: i.series, cover_url: i.cover_url }} />}
          <DiscoverToReadButton item={{ asin: i.asin, lang: i.lang, title: i.title, author: i.author, series: i.series, cover_url: i.cover_url }} />
        </div>
      </div>
    </li>
  )
}

function Lists({ path, hide, setHide, controls = true }: { path: string; controls?: boolean } & HideProps) {
  const { data, error, loading, reload } = useData<Item[]>(`/discover/${path}`)
  const listed = useContext(ToReadAsins).hide
  const [mine, setMine] = useState(true)
  if (error && !data) return <ErrorBox message={error} onRetry={reload} />
  if (loading && !data) return <SkeletonGrid />
  const shown = (data ?? []).filter(visible(hide, listed))
  const personal = shown.some(i => i.fits)
  // Passende Titel zuerst, sonst die Reihenfolge von Audible (stabile Sortierung)
  const list = mine && personal ? [...shown].sort((a, b) => Number(!a.fits) - Number(!b.fits)) : shown
  return (
    <>
      <div className={`mb-4 flex flex-wrap gap-x-6 ${controls ? '' : 'hidden'}`}>
        <HideKnown hide={hide} setHide={setHide} />
        {personal && (
          <label className="inline-flex min-h-9 items-center gap-2 text-sm text-muted" title={t('Nach den Genres der Bücher, die du gehört hast')}>
            <input type="checkbox" checked={mine} onChange={e => setMine(e.target.checked)} className="h-4 w-4 accent-[var(--accent)]" />
            {t('Passend zu dir zuerst')}
          </label>
        )}
      </div>
      {list.length === 0 ? ALL_KNOWN
        : <ul className={GRID}>{list.map(i => <ItemCard key={i.asin} i={i} />)}</ul>}
    </>
  )
}

type Lang = 'all' | 'en' | 'de'
const LANG_NAME = { en: t('Englisch'), de: t('Deutsch') } as const

/** Bestseller und Neuerscheinungen von Audible. Die Sprache kommt vom Schalter oben; bei «Beide» stehen die Listen untereinander. */
function Toplists({ lang, hide, setHide }: { lang: Lang } & HideProps) {
  const [kind, setKind] = useState<'bestseller' | 'neu'>('bestseller')
  const all = useLangs()
  const langs = lang === 'all' ? all : [lang]
  return (
    <>
      <Tabs label={t('Toplisten')} value={kind} onChange={setKind} items={[{ key: 'bestseller', label: t('Bestseller') }, { key: 'neu', label: t('Neu erschienen') }]} />
      {langs.map((l, i) => (
        <section key={kind + l} className={i > 0 ? 'mt-10' : ''}>
          {langs.length > 1 && <h2 className="mb-3 text-lg font-semibold">{LANG_NAME[l]}</h2>}
          <Lists path={`${kind}/${l}`} hide={hide} setHide={setHide} controls={i === 0} />
        </section>
      ))}
    </>
  )
}

/** Vorschläge in der oben gewählten Sprache (der Sprachschalter gilt auch hier). */
function ForYou({ lang, hide, setHide }: { lang: Lang } & HideProps) {
  const { data, error, loading, reload } = useData<{ based_on: number; rated: number; tag_profile: { tag: string; share: number }[]; items: Item[] }>('/discover/foryou')
  const { data: count } = useData<{ open: number }>('/ratings/count')
  const listed = useContext(ToReadAsins).hide
  if (error && !data) return <ErrorBox message={error} onRetry={reload} />
  if (loading && !data) return (
    <>
      <p className="mb-3 flex items-center gap-2 text-sm text-muted"><Loader2 size={14} aria-hidden className="animate-spin" />{t('Fragt Audible nach ähnlichen Titeln zu deinen gehörten Büchern. Beim ersten Mal dauert das bis zu einer halben Minute.')}</p>
      <SkeletonGrid />
    </>
  )
  if (!data?.based_on) return <Empty title={t('Noch nichts gehört')} text={t('Die Vorschläge entstehen aus den Büchern, die du in Audiobookshelf gehört hast. Ein einzelnes angefangenes Buch reicht noch nicht: Sobald du ein Buch fertig gehört hast, erscheinen sie hier.')} />
  const inLang = data.items.filter(i => lang === 'all' || i.lang === lang)
  const list = inLang.filter(visible(hide, listed))
  return (
    <>
      <p className="mb-3 text-sm text-muted">{t('Serien und Einzelbücher, die Audible zu')}{' '}{data.based_on} {t('von dir gehörten Serien und Büchern vorschlägt. Je mehr Bände du von einer Serie gehört hast, desto stärker zählt sie; ganz gehörte und gut bewertete Serien zählen am meisten, abgebrochene und schlecht bewertete drücken Ähnliches nach unten. Dazu kommen Unterarten wie LitRPG oder Isekai, was andere hier mit ähnlichem Geschmack gehört haben, bekannte Autoren und Sprecher, längere Serien und die Audible-Bewertung.')}</p>
      {count && count.open > 0 && (
        <Link to="/bewertungen" className="mb-3 flex items-center gap-3 rounded-xl border border-line bg-surface px-4 py-3 text-sm hover:border-accent">
          <Star size={18} aria-hidden style={{ color: 'var(--warn)' }} className="shrink-0" />
          <span className="flex-1"><strong className="tabular">{count.open}</strong> {t('gehörte')}{' '}{count.open === 1 ? t('Buch ist') : t('Bücher sind')} {t('noch nicht bewertet. Mit Sternen werden die Vorschläge genauer.')}</span>
          <span className="shrink-0 font-medium text-accent">{t('Bewerten')}</span>
        </Link>
      )}
      {data.tag_profile?.length > 0 && (
        <p className="mb-3 flex flex-wrap items-center gap-1.5 text-xs text-muted">
          <span>{t('Dein Profil:')}</span>
          {data.tag_profile.map(tx => <span key={tx.tag} className="rounded-full border border-line px-2 py-0.5">{tx.tag} <span className="tabular text-faint">{Math.round(tx.share * 100)} %</span></span>)}
        </p>
      )}
      <div className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-2"><HideKnown hide={hide} setHide={setHide} /></div>
      {inLang.length === 0 ? <Empty title={lang === 'all' ? t('Noch keine Vorschläge') : t('Keine Vorschläge auf {{v0}}', { v0: LANG_NAME[lang] })} text={t('Vorschläge entstehen nur aus Büchern, die du in dieser Sprache gehört hast. Wähle oben eine andere Sprache.')} />
        : list.length === 0 ? ALL_KNOWN
        : <ul className={GRID}>{list.map(i => <ItemCard key={i.asin + i.lang} i={i} asSeries />)}</ul>}
    </>
  )
}

function FromAuthors({ lang }: { lang: Lang }) {
  const { data, error, loading, reload } = useData<AuthorSeries[]>('/discover/authors')
  if (error && !data) return <ErrorBox message={error} onRetry={reload} />
  if (loading && !data) return <SkeletonGrid />
  const list = (data ?? []).filter(s => lang === 'all' || s.lang === lang)
  if (!data?.length) return <Empty title={t('Noch keine Vorschläge')} text={t('Die Vorschläge entstehen aus dem Autorenkatalog. Die Prüfung läuft unter Aufgaben, «Autorenkatalog prüfen», und braucht beim ersten Mal etwa eine halbe Stunde.')} />
  return (
    <>
      <p className="mb-3 text-sm text-muted">{t('Serien von Autoren, die du oft im Bestand und gehört hast, und die noch fehlen. Meistgehörte Autoren zuerst.')}</p>
      {list.length === 0 && <Empty title={lang === 'all' ? t('Noch keine Vorschläge') : t('Keine Vorschläge auf {{v0}}', { v0: LANG_NAME[lang] })} text={t('Wähle oben eine andere Sprache.')} />}
      <ul className={GRID}>
        {list.map(s => (
          <li key={s.series_asin + s.lang} className="flex flex-col overflow-hidden rounded-xl border border-line bg-surface">
            <Cover src={s.cover_url} alt={s.series_title} />
            <div className="flex flex-1 flex-col gap-1.5 p-3">
              <p className="line-clamp-2 text-sm font-semibold leading-snug">{s.series_title}</p>
              <p className="truncate text-xs text-muted">{s.author}</p>
              <p className="flex items-center gap-2 text-xs text-muted"><LangTag lang={s.lang} /><span className="tabular">{fmtSeriesSize(s.book_count, s.series_min)}</span></p>
              <div className="mt-auto flex flex-col gap-1.5 pt-1">
                <WatchButton watched={s.watched} item={{ asin: s.series_asin, lang: s.lang, title: s.series_title, author: s.author, cover_url: s.cover_url, kind: 'series' }} />
                <DiscoverToReadButton item={{ asin: s.series_asin, lang: s.lang, title: s.series_title, author: s.author, cover_url: s.cover_url, kind: 'series' }} />
              </div>
            </div>
          </li>
        ))}
      </ul>
    </>
  )
}

/** Freie Suche im Audible-Katalog. Treffer lassen sich wie die Toplisten hinzufügen. */
function AudibleSearch({ q, lang, hide, setHide }: { q: string; lang: Lang } & HideProps) {
  const { data, error, loading, reload } = useData<Item[]>(`/discover/search?q=${encodeURIComponent(q)}&lang=${lang}`)
  const listed = useContext(ToReadAsins).hide
  if (error && !data) return <ErrorBox message={error} onRetry={reload} />
  if (loading && !data) return <SkeletonGrid />
  if (!data?.length) return <Empty title={t('Keine Treffer bei Audible')} text={t('Zu «{{v0}}» hat Audible nichts in {{v1}} gefunden. Versuche es mit weniger Wörtern, nur dem Titel oder nur dem Autor.', { v0: q, v1: lang === 'de' ? 'Deutsch' : lang === 'en' ? t('Englisch') : t('Deutsch oder Englisch') })} />
  const list = data.filter(visible(hide, listed))
  return (
    <>
      <p className="mb-3 text-sm text-muted">{data.length} {t('Treffer bei Audible, beste zuerst. Ein Buch einer Reihe bringt mit «Hinzufügen» die ganze Reihe mit.')}</p>
      <div className="mb-4 flex flex-wrap gap-x-6"><HideKnown hide={hide} setHide={setHide} /></div>
      {list.length === 0 ? ALL_KNOWN : <ul className={GRID}>{list.map(i => <ItemCard key={i.asin + i.lang} i={i} />)}</ul>}
    </>
  )
}

const SUB: Record<Tab, string> = {
  foryou: t('Vorschläge aus deinem Bestand und deinen Bewertungen.'),
  authors: t('Serien deiner meistgehörten Autoren, die dir noch fehlen.'),
  top: t('Bestseller und Neuerscheinungen bei Audible.'),
}

export default function DiscoverPage({ tab }: { tab: Tab }) {
  const [text, setText] = useState('')
  const [query, setQuery] = useState('')
  const [lang, setLang] = useState<Lang>('all')
  const langs = useLangs()
  const [hide, setHide] = useState<Hide>({ known: false, heard: true, toread: true })
  const submit = (e: FormEvent) => { e.preventDefault(); setQuery(text.trim()) }
  const clear = () => { setText(''); setQuery('') }
  const toread = useData<string[]>('/toread/asins')
  // Filter nach dem Stand beim Laden: was man hier gerade auf die Liste setzt, bleibt bis zum nächsten Laden sichtbar
  const [atLoad, setAtLoad] = useState<Set<string> | null>(null)
  if (toread.data && !atLoad) setAtLoad(new Set(toread.data))
  return (
    <ToReadAsins.Provider value={{ asins: new Set(toread.data ?? []), hide: atLoad ?? new Set(), reload: toread.reload }}>
    <Page title={t('Entdecken')} subtitle={t('{{v0}} «Hinzufügen» nimmt einen Titel in die Bibliothek auf und sucht ihn, «Zu To Read» setzt seine Serie zusätzlich auf deine Liste.', { v0: SUB[tab] })}>
      <form onSubmit={submit} role="search" className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative min-w-56 flex-1 sm:max-w-md">
          <label htmlFor="audible-search" className="sr-only">{t('Audible durchsuchen')}</label>
          <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" aria-hidden />
          <input id="audible-search" type="search" value={text} onChange={e => setText(e.target.value)} placeholder={t('Audible durchsuchen: Titel, Autor, Serie')}
            className="h-10 w-full rounded-lg border border-line bg-surface pl-9 pr-9 text-sm placeholder:text-faint [&::-webkit-search-cancel-button]:hidden" />
          {text && (
            <button type="button" onClick={clear} aria-label={t('Suche leeren')}
              className="absolute right-1 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded text-faint hover:text-text"><X size={15} aria-hidden /></button>
          )}
        </div>
        {langs.length > 1 && <div className="flex gap-1 rounded-lg border border-line bg-surface p-1" role="group" aria-label={t('Sprache')} title={t('Gilt für die Suche und alle Listen')}>
          {(['all', ...langs] as const).map(l => (
            <button key={l} type="button" onClick={() => setLang(l)} aria-pressed={lang === l}
              className={`min-h-8 rounded-md px-3 text-sm font-medium ${lang === l ? 'bg-surface-2 text-text' : 'text-muted hover:text-text'}`}>
              {l === 'all' ? t('Beide') : l === 'en' ? t('Englisch') : t('Deutsch')}
            </button>
          ))}
        </div>}
        <button type="submit" disabled={text.trim().length < 2}
          className="flex h-10 items-center gap-1.5 rounded-lg bg-accent px-4 text-sm font-semibold text-on-accent disabled:opacity-50">
          <Search size={15} aria-hidden />{t('Bei Audible suchen')}
        </button>
      </form>
      <FollowOnAdd />
      {query ? (
        <>
          <button type="button" onClick={clear} className="mb-4 inline-flex min-h-9 items-center gap-1.5 text-sm text-muted hover:text-text">
            <X size={14} aria-hidden />{t('Suche schliessen, zurück zu den Listen')}
          </button>
          <AudibleSearch key={query + lang} q={query} lang={lang} hide={hide} setHide={setHide} />
        </>
      ) : (
        <>
          {tab === 'foryou' ? <ForYou lang={lang} hide={hide} setHide={setHide} /> : tab === 'authors' ? <FromAuthors lang={lang} />
            : <Toplists lang={lang} hide={hide} setHide={setHide} />}
        </>
      )}
    </Page>
    </ToReadAsins.Provider>
  )
}
