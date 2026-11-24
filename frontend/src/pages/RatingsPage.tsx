import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Loader2, Undo2 } from 'lucide-react'
import { Stars } from '../Stars'
import { api, fmtDate, useData } from '../api'
import { Chip, Cover, Empty, ErrorBox, Page, SkeletonGrid } from '../ui'
import { locale, t } from '../i18n'

interface RBook {
  id: number; asin: string | null; lang: string; title: string; author: string | null; sequence: string | null; cover: string | null
  series: string | null; series_id: number | null; edition_id: number | null; finished_at?: string; stars?: number | null; rated_at?: string
}
interface Group { key: string; edition_id: number | null; series_id: number | null; series: string | null; last: string; books: RBook[] }
interface TagTop { tag: string; generic: boolean; count: number; average: number; books: RBook[] }

type Tab = 'open' | 'mine' | 'tags'

function Line({ b, children, sub, focus }: { b: RBook; children: React.ReactNode; sub?: React.ReactNode; focus?: boolean }) {
  return (
    <li className={`flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line px-3 py-2 first:border-t-0 sm:px-4 ${focus ? 'bg-surface-2 shadow-[inset_3px_0_0_var(--warn)]' : ''}`}>
      <Cover src={b.cover} alt={b.title} className="w-11 shrink-0 rounded-md" />
      <div className="min-w-0 flex-1 basis-40">
        <p className="truncate text-sm font-medium">{b.title}</p>
        <p className="truncate text-xs text-muted">{[b.sequence && t('Band {{v0}}', { v0: b.sequence }), b.author].filter(Boolean).join(' · ')}</p>
        {sub}
      </div>
      <div className="flex items-center gap-1">{children}</div>
    </li>
  )
}

function OpenTab({ onChange }: { onChange: () => void }) {
  const { data, error, loading, reload } = useData<Group[]>('/ratings/open')
  const [gone, setGone] = useState<Set<number>>(new Set())
  const [err, setErr] = useState<string | null>(null)
  // Aus der Meldung «Wie war es?»: dieses Buch zuoberst und hervorgehoben
  const focus = Number(useSearchParams()[0].get('buch')) || null
  if (error && !data) return <ErrorBox message={error} onRetry={reload} />
  if (loading && !data) return <SkeletonGrid n={6} />
  const groups = (data ?? []).map(g => ({ ...g, books: g.books.filter(b => !gone.has(b.id)) })).filter(g => g.books.length)
    .sort((a, b) => Number(b.books.some(x => x.id === focus)) - Number(a.books.some(x => x.id === focus)))
  if (!groups.length) return <Empty title={t('Alles bewertet')} text={t('Sobald du ein weiteres Buch fertig gehört hast, erscheint es hier.')} />
  const rate = async (ids: number[], call: () => Promise<unknown>) => {
    setErr(null)
    setGone(s => new Set([...s, ...ids]))
    try { await call(); onChange() } catch (e) {
      setErr((e as Error).message)
      setGone(s => { const n = new Set(s); ids.forEach(i => n.delete(i)); return n })
    }
  }
  const one = (b: RBook, stars: number | null) => rate([b.id], () => api(`/ratings/book/${b.id}`, { method: 'POST', body: JSON.stringify({ stars }) }))
  const all = (g: Group, stars: number | null) => rate(g.books.map(b => b.id), () => api(`/ratings/edition/${g.edition_id}`, { method: 'POST', body: JSON.stringify({ stars }) }))
  return (
    <>
      <p className="mb-4 text-sm text-muted">{t('Bücher, die du fertig gehört und noch nicht bewertet hast. Gut bewertete Serien zählen bei «Für dich» mehr, Serien mit 1 oder 2 Sternen drücken Ähnliches nach unten. «Nicht bewerten» nimmt ein Buch ohne Wirkung aus der Liste.')}</p>
      {err && <ErrorBox message={err} plain />}
      <div className="flex flex-col gap-4">
        {groups.map(g => (
          <section key={g.key} className="overflow-hidden rounded-xl border border-line bg-surface">
            {g.series && (
              <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line bg-surface-2 px-3 py-1.5 sm:px-4">
                {g.series_id ? <Link to={`/serie/${g.series_id}`} className="font-semibold hover:text-accent">{g.series}</Link> : <span className="font-semibold">{g.series}</span>}
                {g.books.length > 1 && g.edition_id && (
                  <div className="flex items-center gap-1 text-xs text-muted">
                    <span>{t('Alle')}{' '}{g.books.length} {t('Bände:')}</span>
                    <Stars value={null} onPick={n => all(g, n)} label={t('Alle Bände von {{v0}} bewerten', { v0: g.series })} />
                  </div>
                )}
              </header>
            )}
            <ul>
              {g.books.map(b => (
                <Line key={b.id} b={b} focus={b.id === focus} sub={b.finished_at && <p className="text-xs text-faint">{t('Fertig gehört')}{' '}{fmtDate(b.finished_at.slice(0, 10))}</p>}>
                  <Stars value={null} onPick={n => one(b, n)} label={`${b.title} bewerten`} />
                  <button type="button" onClick={() => one(b, null)} className="min-h-11 px-2 text-xs text-muted hover:text-text">{t('Nicht bewerten')}</button>
                </Line>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </>
  )
}

function MineTab({ onChange }: { onChange: () => void }) {
  const { data, error, loading, reload } = useData<RBook[]>('/ratings/mine')
  const [filter, setFilter] = useState<number | 'none' | 'all'>('all')
  const [err, setErr] = useState<string | null>(null)
  if (error && !data) return <ErrorBox message={error} onRetry={reload} />
  if (loading && !data) return <SkeletonGrid n={6} />
  const list = data ?? []
  if (!list.length) return <Empty title={t('Noch keine Bewertungen')} text={t('Unter «Hören», «Bewerten» bewertest du deine gehörten Bücher.')} />
  const count = (f: number | 'none') => list.filter(b => (f === 'none' ? b.stars == null : b.stars === f)).length
  const shown = list.filter(b => filter === 'all' || (filter === 'none' ? b.stars == null : b.stars === filter))
  const change = async (call: () => Promise<unknown>) => {
    setErr(null)
    try { await call(); reload(); onChange() } catch (e) { setErr((e as Error).message) }
  }
  return (
    <>
      <div className="mb-4 flex gap-2 overflow-x-auto pb-1">
        <Chip active={filter === 'all'} onClick={() => setFilter('all')} count={list.length}>{t('Alle')}</Chip>
        {[5, 4, 3, 2, 1].map(n => count(n) > 0 && <Chip key={n} active={filter === n} onClick={() => setFilter(n)} count={count(n)}>{'★'.repeat(n)}</Chip>)}
        {count('none') > 0 && <Chip active={filter === 'none'} onClick={() => setFilter('none')} count={count('none')}>{t('Nicht bewertet')}</Chip>}
      </div>
      {err && <ErrorBox message={err} plain />}
      <ul className="overflow-hidden rounded-xl border border-line bg-surface">
        {shown.map(b => (
          <Line key={b.id} b={b} sub={b.series && <p className="truncate text-xs text-faint">{b.series}</p>}>
            <Stars value={b.stars} onPick={n => change(() => api(`/ratings/book/${b.id}`, { method: 'POST', body: JSON.stringify({ stars: n }) }))} label={`${b.title} bewerten`} />
            <button type="button" title={t('Zurück unter «Bewerten»')} aria-label={t('Bewertung von {{v0}} zurücknehmen', { v0: b.title })}
              onClick={() => change(() => api(`/ratings/book/${b.id}`, { method: 'DELETE' }))}
              className="grid h-11 w-11 place-items-center rounded-md text-muted hover:text-text"><Undo2 size={18} aria-hidden /></button>
          </Line>
        ))}
      </ul>
    </>
  )
}

function TagsTab() {
  const { data, error, loading, reload } = useData<TagTop[]>('/ratings/tags')
  const [open, setOpen] = useState<Set<string>>(new Set())
  if (error && !data) return <ErrorBox message={error} onRetry={reload} />
  if (loading && !data) return <p className="flex items-center gap-2 text-sm text-muted"><Loader2 size={14} aria-hidden className="animate-spin" />{t('Holt die Tags deiner Bücher von Audible. Beim ersten Mal dauert das einige Sekunden.')}</p>
  if (!data?.length) return <Empty title={t('Noch keine Topliste')} text={t('Bewerte zuerst ein paar Bücher unter «Hören», «Bewerten». Die Topliste ordnet sie nach Unterart (LitRPG, Cultivation, Isekai …).')} />
  return (
    <>
      <p className="mb-4 text-sm text-muted">{t('Deine bewerteten Bücher je Unterart, beste zuerst. Die Unterarten erkennt ABW aus Titel, Audible-Kategorie und Beschreibung sowie den Leser-Genres von Goodreads; ein Buch kann in mehreren stehen.')}</p>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {data.map(tx => {
          const all = open.has(tx.tag)
          return (
            <section key={tx.tag} className="overflow-hidden rounded-xl border border-line bg-surface">
              <header className="flex items-baseline justify-between gap-2 border-b border-line bg-surface-2 px-4 py-2">
                <h2 className={`font-semibold ${tx.generic ? 'text-muted' : ''}`}>{tx.tag}</h2>
                <span className="text-xs text-muted tabular">{tx.count} {tx.count === 1 ? t('Buch') : t('Bücher')} · Ø {tx.average.toLocaleString(locale())} ★</span>
              </header>
              <ol>
                {(all ? tx.books : tx.books.slice(0, 5)).map((b, i) => (
                  <li key={b.id} className="flex items-center gap-3 border-t border-line px-4 py-2 first:border-t-0">
                    <span className="w-5 text-right text-sm text-faint tabular">{i + 1}</span>
                    <Cover src={b.cover} alt={b.title} className="w-9 shrink-0 rounded" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm">{b.title}</p>
                      {b.series && <p className="truncate text-xs text-faint">{b.series}{b.sequence ? t(', Band {{v0}}', { v0: b.sequence }) : ''}</p>}
                    </div>
                    <span className="shrink-0 text-sm" style={{ color: 'var(--warn)' }} aria-label={t('{{v0}} Sterne', { v0: b.stars })}>{'★'.repeat(b.stars ?? 0)}</span>
                  </li>
                ))}
              </ol>
              {tx.books.length > 5 && (
                <button type="button" onClick={() => setOpen(s => { const n = new Set(s); if (all) n.delete(tx.tag); else n.add(tx.tag); return n })}
                  className="min-h-10 w-full border-t border-line text-sm font-medium text-accent hover:underline">
                  {all ? t('Weniger zeigen') : t('Alle {{v0}} zeigen', { v0: tx.books.length })}
                </button>
              )}
            </section>
          )
        })}
      </div>
    </>
  )
}

const TITLE: Record<Tab, [string, string]> = {
  open: [t('Bewerten'), t('Bewerte, was du gehört hast. Die Sterne machen die Vorschläge unter «Für dich» genauer.')],
  mine: [t('Meine Bewertungen'), t('Deine Sterne für gehörte Bücher. Ändern geht hier, zurücknehmen schiebt das Buch wieder unter «Bewerten».')],
  tags: [t('Topliste je Tag'), t('Deine bewerteten Bücher je Unterart, beste zuerst.')],
}

/** Register kommen aus der Adresse: /bewertungen (offen, unter «Hören»), /bewertungen/meine und /bewertungen/tags (unter «Rückblick»). */
export default function RatingsPage({ tab }: { tab: Tab }) {
  const { reload } = useData<{ open: number }>('/ratings/count')
  const [title, subtitle] = TITLE[tab]
  return (
    <Page title={title} subtitle={subtitle}>
      {tab === 'open' ? <OpenTab onChange={reload} /> : tab === 'mine' ? <MineTab onChange={reload} /> : <TagsTab />}
    </Page>
  )
}
