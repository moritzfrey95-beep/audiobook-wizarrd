import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Search } from 'lucide-react'
import { useData } from '../api'
import { Chip, Cover, Empty, ErrorBox, LangTag, Page, SkeletonGrid } from '../ui'
import { t } from '../i18n'

export interface AuthorTile {
  key: string; name: string; en: number; de: number; hours: number; series_count: number; more_series: number
  series: { id: number; name: string; cover: string | null; owned: number }[]
}

export default function AuthorsPage() {
  const { data, error, loading, reload } = useData<AuthorTile[]>('/authors')
  const [q, setQ] = useState('')
  const [only, setOnly] = useState<'all' | 'both' | 'more'>('all')
  const [shown, setShown] = useState(60)
  const list = useMemo(() => (data ?? []).filter(a =>
    a.name.toLowerCase().includes(q.trim().toLowerCase()) &&
    (only === 'all' || (only === 'both' ? a.en > 0 && a.de > 0 : a.more_series > 0))), [data, q, only])
  return (
    <Page title={t('Autoren')} subtitle={data ? t('{{v0}} von {{v1}} Autoren, sortiert nach Anzahl Bücher', { v0: list.length, v1: data.length }) : t('Autoren werden geladen')}>
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <div className="relative min-w-56 flex-1 sm:max-w-sm">
          <label htmlFor="author-search" className="sr-only">{t('Autor suchen')}</label>
          <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" aria-hidden />
          <input id="author-search" value={q} onChange={e => setQ(e.target.value)} placeholder={t('Autor suchen')}
            className="h-10 w-full rounded-lg border border-line bg-surface pl-9 pr-3 text-sm placeholder:text-faint" />
        </div>
        <div className="flex flex-wrap gap-2">
          <Chip active={only === 'all'} onClick={() => setOnly('all')}>{t('Alle')}</Chip>
          <Chip active={only === 'both'} onClick={() => setOnly('both')}>{t('Deutsch und Englisch vorhanden')}</Chip>
          <Chip active={only === 'more'} onClick={() => setOnly('more')}>{t('Mit weiteren Serien bei Audible')}</Chip>
        </div>
      </div>
      {error && !data ? <ErrorBox message={error} onRetry={reload} /> : loading && !data ? <SkeletonGrid n={12} />
        : list.length === 0 ? <Empty title={t('Kein Autor gefunden')} text={t('Prüfe die Schreibweise oder wähle den Filter «Alle».')} />
        : (
          <>
            <ul className="card-grid grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
              {list.slice(0, shown).map(a => (
                <li key={a.key}>
                  <Link to={`/autor/${a.key}`} className="group flex h-full gap-4 rounded-xl border border-line bg-surface p-4 transition duration-200 hover:border-faint hover:shadow-[var(--shadow)]">
                    <div className="flex w-24 shrink-0 items-center">
                      {a.series.length ? (
                        <div className="relative h-24 w-24">
                          {a.series.slice(0, 3).map((s, i) => (
                            <div key={s.id} className="absolute w-[72px] overflow-hidden rounded-md border border-bg shadow-md"
                              style={{ left: i * 12, top: i * 12, zIndex: 3 - i }}>
                              <Cover src={s.cover} alt={s.name} />
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className="flex h-20 w-20 items-center justify-center rounded-full bg-surface-2 font-[family-name:var(--font-display)] text-2xl font-bold text-muted">
                          {a.name.split(' ').map(w => w[0]).slice(0, 2).join('')}
                        </div>
                      )}
                    </div>
                    <div className="flex min-w-0 flex-1 flex-col gap-2">
                      <h2 className="truncate text-base font-semibold group-hover:text-accent">{a.name}</h2>
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted">
                        <span className="inline-flex items-center gap-1.5"><LangTag lang="en" /><span className="tabular">{a.en}</span></span>
                        <span className="inline-flex items-center gap-1.5"><LangTag lang="de" /><span className="tabular">{a.de}</span></span>
                        <span className="tabular" title={t('Laufzeit im Bestand; Serien mit mehreren Fassungen zählen mit deren mittlerer Laufzeit')}>{a.hours} h</span>
                      </div>
                      <p className="mt-auto text-xs text-muted">
                        {a.series_count} {a.series_count === 1 ? t('Serie') : t('Serien')} {t('im Bestand')}
                        {a.more_series > 0 && <span style={{ color: 'var(--info)' }}> · {a.more_series} {t('weitere bei Audible')}</span>}
                      </p>
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
            {list.length > shown && (
              <div className="mt-6 flex justify-center">
                <button type="button" onClick={() => setShown(shown + 120)} className="min-h-10 rounded-lg border border-line bg-surface px-5 text-sm font-semibold hover:border-faint">{t('Weitere Autoren zeigen')}</button>
              </div>
            )}
          </>
        )}
    </Page>
  )
}
