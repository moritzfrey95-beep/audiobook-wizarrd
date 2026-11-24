import { Link, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { fmtDate, useData, type Series } from '../api'
import { SeriesCard } from '../SeriesCard'
import { Cover, ErrorBox, GRID, LangTag, Page, Section } from '../ui'
import { WatchButton } from '../Watch'
import { FollowButton } from '../Follow'
import { useLangs } from '../session'
import { t } from '../i18n'

interface Cat { lang: string; series_asin: string; series_title: string; book_count: number; cover_url: string | null; series_id: number | null; watched: boolean }
interface Detail {
  key: string; name: string; series: Series[]; catalog: Cat[]; catalog_fetched: string | null
  singles: { id: number; title: string; lang: string; cover: string | null; finished: boolean; progress: number | null; owned: boolean }[]
}

export default function AuthorDetailPage() {
  const { key } = useParams()
  const { data: a, error, reload } = useData<Detail>(`/authors/${key}`)
  const langs = useLangs()
  if (error && !a) return <Page title={t('Autor')}><ErrorBox message={error} onRetry={reload} /></Page>
  if (!a) return <Page title={t('Autor wird geladen')}><div className="skeleton h-40 rounded-2xl" /></Page>
  const more = a.catalog.filter(c => !c.series_id)
  return (
    <Page title={a.name} actions={<FollowButton kind="author" id={a.key} />} subtitle={<Link to="/autoren" className="inline-flex items-center gap-1.5 hover:text-text"><ArrowLeft size={14} aria-hidden />{t('Alle Autoren')}</Link>}>
      <Section title={t('Serien im Bestand')} hint={`${a.series.length} ${a.series.length === 1 ? t('Serie') : t('Serien')}`}>
        {a.series.length ? <div className={GRID}>{a.series.map(s => <SeriesCard key={s.id} s={s} />)}</div>
          : <p className="text-sm text-muted">{t('Von diesem Autor ist keine Serie im Bestand.')}</p>}
      </Section>
      {langs.map(lang => {
        const items = more.filter(c => c.lang === lang)
        return (
          <Section key={lang} title={t('Weitere Serien bei Audible, {{v0}}', { v0: lang === 'en' ? t('Englisch') : t('Deutsch') })}
            hint={a.catalog_fetched ? t('Stand {{v0}}', { v0: fmtDate(a.catalog_fetched) }) : t('Autorenkatalog noch nicht geprüft')}>
            {items.length === 0 ? <p className="text-sm text-muted">{a.catalog_fetched ? t('Keine weiteren Serien in dieser Sprache gefunden.') : t('Die Prüfung läuft unter Aufgaben, «Autorenkatalog prüfen».')}</p> : (
              <ul className={GRID}>
                {items.map(c => (
                  <li key={c.series_asin} className="flex flex-col overflow-hidden rounded-xl border border-dashed border-line bg-surface">
                    <Cover src={c.cover_url} alt={c.series_title} />
                    <div className="flex flex-1 flex-col gap-2 p-3">
                      <p className="line-clamp-2 text-sm font-semibold leading-snug">{c.series_title}</p>
                      <p className="flex items-center gap-2 text-xs text-muted"><LangTag lang={c.lang} />{c.book_count} {c.book_count === 1 ? t('Band') : t('Bände')}</p>
                      <div className="mt-auto"><WatchButton watched={c.watched} item={{ asin: c.series_asin, lang: c.lang, title: c.series_title, author: a.name, cover_url: c.cover_url, kind: 'series' }} /></div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Section>
        )
      })}
      {a.singles.length > 0 && (
        <Section title={t('Einzelbücher')} hint={t('{{v0}} Bücher ohne Serie', { v0: a.singles.length })}>
          <ul className={GRID}>
            {a.singles.map(b => (
              <li key={b.id} className={`overflow-hidden rounded-xl border border-line bg-surface ${b.owned ? '' : 'border-dashed'}`}>
                <Cover src={b.cover} alt={b.title} />
                <div className="p-3"><p className="line-clamp-2 text-sm font-semibold leading-snug">{b.title}</p>
                  <p className="mt-1 flex items-center gap-2 text-xs text-muted"><LangTag lang={b.lang} />{b.finished ? t('Gehört') : (b.progress ?? 0) > 0.01 ? t('{{v0}} % gehört', { v0: Math.round((b.progress ?? 0) * 100) }) : b.owned ? t('Im Bestand') : t('Hinzugefügt, wird gesucht')}</p></div>
              </li>
            ))}
          </ul>
        </Section>
      )}
    </Page>
  )
}
