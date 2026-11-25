import { useState } from 'react'
import { Link } from 'react-router-dom'
import { fmtDate, useData } from '../api'
import { Chip, Cover, Empty, ErrorBox, LangTag, Page } from '../ui'
import { locale, t } from '../i18n'
import { CalendarFeed } from '../CalendarFeed'

interface Entry { series_id: number; series: string; lang: string; monitored: boolean; title: string; sequence: string | null; release_date: string | null; cover: string | null; author: string }

export default function CalendarPage() {
  const { data, error, loading, reload } = useData<Entry[]>('/calendar')
  const [all, setAll] = useState(false)
  const list = (data ?? []).filter(e => all || e.monitored)
  const groups = new Map<string, Entry[]>()
  for (const e of list) {
    const key = e.release_date ? new Date(e.release_date).toLocaleDateString(locale(), { month: 'long', year: 'numeric' }) : t('Angekündigt, Termin offen')
    groups.set(key, [...(groups.get(key) ?? []), e])
  }
  const days = (d: string) => Math.ceil((new Date(d).getTime() - Date.now()) / 86400000)
  return (
    <Page title={t('Kalender')} subtitle={data ? t('{{v0}} angekündigte Bände', { v0: list.length }) : t('Termine werden geladen')}>
      <div className="mb-4"><CalendarFeed /></div>
      <div className="mb-5 flex gap-2">
        <Chip active={!all} onClick={() => setAll(false)}>{t('Überwachte Fassungen')}</Chip>
        <Chip active={all} onClick={() => setAll(true)}>{t('Alle Sprachen')}</Chip>
      </div>
      {error && !data ? <ErrorBox message={error} onRetry={reload} /> : loading && !data ? <div className="skeleton h-64 rounded-2xl" />
        : list.length === 0 ? <Empty title={t('Keine angekündigten Bände')} text={t('Sobald Audible einen neuen Band einer überwachten Serie ankündigt, steht er hier. Die Serien werden alle sechs Stunden geprüft.')} />
        : [...groups.entries()].map(([month, items]) => (
          <section key={month} className="mb-8">
            <h2 className="sticky top-0 z-10 lg:top-[60px] -mx-1 mb-3 bg-bg/95 px-1 py-2 text-lg font-semibold capitalize backdrop-blur">{month} <span className="tabular text-sm font-normal text-muted">{items.length}</span></h2>
            <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
              {items.map(e => (
                <li key={e.series_id + e.lang + e.title}>
                  <Link to={`/serie/${e.series_id}`} className="flex gap-3 rounded-xl border border-line bg-surface p-3 transition hover:border-faint">
                    <Cover src={e.cover} alt={e.title} className="h-20 w-20 shrink-0 rounded-lg" />
                    <div className="min-w-0 flex-1">
                      <p className="flex items-center gap-2 text-xs text-muted"><LangTag lang={e.lang} /><span className="truncate">{e.series}{e.sequence ? t(', Band {{v0}}', { v0: e.sequence }) : ''}</span></p>
                      <p className="mt-1 line-clamp-2 text-[15px] font-semibold leading-snug">{e.title}</p>
                      <p className="mt-1 text-xs text-muted">{e.author}</p>
                    </div>
                    {e.release_date && (
                      <div className="shrink-0 text-right">
                        <p className="tabular text-sm font-semibold" style={{ color: 'var(--info)' }}>{fmtDate(e.release_date)}</p>
                        <p className="tabular text-xs text-muted">{days(e.release_date) === 1 ? t('morgen') : t('in {{n}} Tagen', { n: days(e.release_date) })}</p>
                      </div>
                    )}
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        ))}
    </Page>
  )
}
