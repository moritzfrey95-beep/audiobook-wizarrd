import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { fmtDate, useData, type Lang, type SingleDetail } from '../api'
import { Badge, Cover, ErrorBox, Page, Section, shownFlags } from '../ui'
import { SearchDialog } from '../SearchDialog'
import { useAdmin } from '../session'
import { ToReadKeys } from '../ToRead'
import { BookCard } from './SeriesDetailPage'
import { t } from '../i18n'

const LANG_NAME: Record<Lang, string> = { en: t('Englisch'), de: t('Deutsch') }
const SEARCHABLE = ['missing', 'searching', 'failed']

/** «Autor – Einzelbücher»: alle Bücher ohne Serie eines Autors, wie eine Serie aufgebaut. */
export default function SinglesDetailPage() {
  const { key } = useParams()
  const { data: g, error, reload } = useData<SingleDetail>(`/singles/${key}`, 30000)
  const toread = useData<string[]>('/toread/keys')
  const admin = useAdmin()
  const [searchBook, setSearchBook] = useState<number | null>(null)
  if (error && !g) return <Page title={t('Einzelbücher')}><ErrorBox message={error} onRetry={reload} /></Page>
  if (!g) return <Page title={t('Einzelbücher werden geladen')}><div className="skeleton h-40 rounded-2xl" /></Page>
  const open = g.books.filter(b => SEARCHABLE.includes(b.state))
  const note = (b: SingleDetail['books'][number]) => b.added_by ? t('Hinzugefügt von {{v0}}{{v1}}', { v0: b.added_by, v1: b.added_at ? t(' am {{date}}', { date: fmtDate(b.added_at) }) : '' }) : ''
  return (
    <main className="mx-auto w-full max-w-[1600px] px-4 pb-16 pt-5 sm:px-6 lg:px-8">
      <Link to="/" className="inline-flex min-h-9 items-center gap-1.5 text-sm font-medium text-muted hover:text-text"><ArrowLeft size={15} aria-hidden />{t('Alle Serien')}</Link>
      <div className="relative mt-3 overflow-hidden rounded-2xl border border-line bg-surface">
        {g.cover && <img src={g.cover} alt="" aria-hidden className="absolute inset-0 h-full w-full scale-125 object-cover opacity-20 blur-3xl" />}
        <div className="relative flex flex-col gap-5 p-5 sm:flex-row sm:p-6">
          <Cover src={g.cover} alt={g.name} className="w-40 shrink-0 rounded-xl shadow-[var(--shadow)] sm:w-52" />
          <div className="flex min-w-0 flex-1 flex-col gap-3">
            <div className="flex flex-wrap gap-1.5">{shownFlags(g.flags).map(f => <Badge key={f} status={f} />)}</div>
            <div>
              <h1 className="text-3xl font-bold sm:text-4xl">{g.name}</h1>
              <Link to={`/autor/${g.author_key}`} className="mt-1 inline-block text-muted underline-offset-4 hover:text-text hover:underline">{g.author}</Link>
            </div>
            <dl className="flex flex-wrap gap-x-8 gap-y-2 text-sm">
              {g.langs.map(l => (
                <div key={l.lang}><dt className="text-faint">{LANG_NAME[l.lang]}</dt><dd className="tabular font-semibold">{l.owned} {t('von')}{' '}{l.total} {t('im Bestand')}</dd></div>
              ))}
              <div><dt className="text-faint">{t('Laufzeit im Bestand')}</dt><dd className="tabular font-semibold">{g.hours} {t('Stunden')}</dd></div>
              <div><dt className="text-faint">{t('Gehört')}</dt><dd className="tabular font-semibold">{g.listened} {t('von')}{' '}{g.owned}</dd></div>
              {g.next_release && <div><dt className="text-faint">{t('Nächstes Buch')}</dt><dd className="tabular font-semibold">{fmtDate(g.next_release)}</dd></div>}
            </dl>
            <p className="mt-auto text-xs text-muted">{t('Diese Karte fasst die Bücher ohne Serie zusammen. Weitere Einzelbücher fügst du unter Entdecken hinzu.')}</p>
          </div>
        </div>
      </div>
      {admin && searchBook !== null && open.length > 0 && (
        <SearchDialog books={open} initial={open.some(b => b.id === searchBook) ? searchBook : open[0].id}
          onClose={() => setSearchBook(null)} onStarted={reload} />
      )}
      <ToReadKeys.Provider value={{ keys: new Set(toread.data ?? []), reload: toread.reload }}>
        {g.langs.map(l => {
          const books = g.books.filter(b => b.lang === l.lang)
          return (
            <Section key={l.lang} title={LANG_NAME[l.lang]} hint={`${books.length} ${books.length === 1 ? t('Buch') : t('Bücher')}`}>
              <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
                {books.map(b => <BookCard key={b.id} b={b} absUrl={g.abs_url} lang={l.lang} canMark={g.can_mark} onSearch={setSearchBook} onChange={reload} note={note(b)} />)}
              </ul>
            </Section>
          )
        })}
      </ToReadKeys.Provider>
    </main>
  )
}
