import { useEffect, useRef, useState } from 'react'
import { Ban, ChevronDown, Download, Eye, EyeOff, Loader2, RefreshCw, Search, X } from 'lucide-react'
import { api, fmtDate, fmtDateTime, type Book } from './api'
import { Button, ErrorBox, tell } from './ui'
import { locale, t } from './i18n'

interface Reason { points: number; text: string }
interface Result {
  source: string; seeders?: number | null; guid: string; title: string; indexer: string | null; size: number | null; age_days: number | null; grabs: number | null
  score: number; verdict: 'sicher' | 'möglich' | 'unsicher' | 'verworfen'; reasons: Reason[]; hidden_until?: string
}
interface BookInfo {
  id: number; title: string; author: string | null; narrator: string | null; sequence: string | null; series: string | null
  lang: string | null; release_date: string | null; runtime_min: number | null
}
interface Response {
  book?: BookInfo; results: Result[]; errors: string[]; queries: string[]; can_start: boolean; searched_at: string; cached: boolean; listed: boolean
  sources: { key: string; name: string; active: boolean }[]; blocked?: string | null
  paused?: { indexer: string; until: string }[]
}

const VERDICT = {
  sicher: { label: t('Sicherer Treffer'), color: 'var(--ok)' },
  möglich: { label: t('Möglicher Treffer'), color: 'var(--ok)' },
  unsicher: { label: t('Unsicher'), color: 'var(--warn)' },
  verworfen: { label: t('Verworfen'), color: 'var(--faint)' },
}
const LANG: Record<string, string> = { en: t('Englisch'), de: t('Deutsch') }
const DAY = 86_400_000
// Kleinbuchstaben ohne Akzente und Satzzeichen, damit «Spider-Man» auch «spider man» findet
const fold = (tx: string) => tx.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^\p{L}\p{N}]+/gu, ' ').trim()
/** Hochgeladen vor dem Erscheinen des Buchs? Ein Tag Spielraum für Zeitzonen; ohne Datum nie. */
const tooOld = (r: Result, book: BookInfo | undefined, searchedAt: string) => {
  if (r.age_days == null || !book?.release_date) return false
  const uploaded = new Date(searchedAt).getTime() - r.age_days * DAY
  return uploaded < new Date(book.release_date).getTime() - DAY
}
const size = (b: number | null) => !b ? '–' : b > 1e9 ? t('{{v0}} GB', { v0: (b / 1e9).toLocaleString(locale(), { maximumFractionDigits: 1 }) }) : t('{{v0}} MB', { v0: Math.round(b / 1e6) })
const age = (d: number | null) => d == null ? '' : d < 1 ? t('heute') : d < 60 ? t('vor {{v0}} Tagen', { v0: d }) : d < 730 ? t('vor {{v0}} Monaten', { v0: Math.round(d / 30) }) : t('vor {{v0}} Jahren', { v0: Math.round(d / 365) })

function ResultRow({ r, canStart, onStart, onHide }: { r: Result; canStart: boolean; onStart: () => void; onHide: () => void }) {
  const [open, setOpen] = useState(r.verdict !== 'verworfen')
  const v = VERDICT[r.verdict]
  return (
    <li className={`rounded-xl border border-line bg-surface p-3 ${r.verdict === 'verworfen' || r.hidden_until ? 'opacity-75' : ''}`}>
      <div className="flex items-start gap-3">
        <div className="flex h-12 w-12 shrink-0 flex-col items-center justify-center rounded-lg text-center" style={{ background: `color-mix(in srgb, ${v.color} 15%, transparent)`, color: v.color }}
          aria-label={t('{{v0}} von 100 Punkten', { v0: r.score })}>
          <span className="tabular text-lg font-bold leading-none">{r.score}</span>
          <span className="text-[9px] font-semibold uppercase leading-tight">{t('Punkte')}</span>
        </div>
        <div className="min-w-0 flex-1">
          <p className="break-words text-sm font-semibold leading-snug">{r.title}</p>
          <p className="mt-1 flex flex-wrap gap-x-3 text-xs text-muted">
            <span className="font-semibold" style={{ color: v.color }}>{v.label}</span>
            <span className="tabular">{size(r.size)}</span>{r.indexer && <span>{r.indexer}</span>}
            <span>{age(r.age_days)}</span>
            {r.hidden_until && <span>{t('ausgeblendet bis')}{' '}{fmtDate(r.hidden_until)}</span>}
          </p>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
          className="inline-flex min-h-9 items-center gap-1 text-xs font-medium text-muted hover:text-text">
          <ChevronDown size={14} aria-hidden className={`transition-transform ${open ? 'rotate-180' : ''}`} />{t('Begründung')}
        </button>
        <button type="button" onClick={onHide} className="mr-auto inline-flex min-h-9 items-center gap-1 text-xs font-medium text-muted hover:text-text"
          title={r.hidden_until ? undefined : t('Einen Monat nicht mehr anzeigen. Die Automatik startet ihn in dieser Zeit nicht.')}>
          {r.hidden_until ? <><Eye size={14} aria-hidden />{t('Wieder einblenden')}</> : <><EyeOff size={14} aria-hidden />{t('1 Monat ausblenden')}</>}
        </button>
        <Button variant={r.verdict === 'sicher' ? 'primary' : 'ghost'} disabled={!canStart} onClick={onStart}
          title={canStart ? undefined : t('Kein Download-Programm eingerichtet')}>
          <Download size={15} aria-hidden />
          {r.verdict === 'verworfen' ? t('Trotzdem herunterladen') : t('Herunterladen')}
        </Button>
      </div>
      {open && (
        <ul className="mt-2 space-y-1 border-t border-line pt-2 text-xs">
          {r.reasons.map((x, i) => (
            <li key={i} className="flex gap-2">
              <span className="tabular w-9 shrink-0 text-right font-semibold" style={{ color: x.points > 0 ? 'var(--ok)' : x.points < 0 ? 'var(--bad)' : 'var(--faint)' }}>
                {x.points > 0 ? t('+{{v0}}', { v0: x.points }) : x.points}
              </span>
              <span className="text-muted">{x.text}</span>
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

/** Geführte Suche: Band wählen, Treffer aller eingeschalteten Quellen mit Punktzahl sehen, Download starten. */
export function SearchDialog({ books, initial, onClose, onStarted, kind = 'audio' }: { books: Book[]; initial: number; onClose: () => void; onStarted: () => void; kind?: 'audio' | 'ebook' }) {
  const ebook = kind === 'ebook'
  const base = ebook ? 'ebook/' : ''
  const ref = useRef<HTMLDialogElement>(null)
  const [bookId, setBookIdRaw] = useState(initial)
  const [data, setData] = useState<Response | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  const [text, setText] = useState('')
  const [hideOld, setHideOld] = useState(true)
  const [showHidden, setShowHidden] = useState(false)
  const setBookId = (id: number) => { setNonce(0); setBookIdRaw(id) }  // anderer Band: wieder mit der gespeicherten Suche beginnen
  useEffect(() => { ref.current?.showModal() }, [])
  useEffect(() => {
    let live = true
    setData(null); setError(null); setActionError(null)
    api<Response>(`/books/${bookId}/${base}search?fresh=${nonce > 0 ? 1 : 0}`).then(d => live && setData(d)).catch(e => live && setError((e as Error).message))
    return () => { live = false }
  }, [bookId, nonce])
  // Fenster sofort schliessen, der Start läuft im Hintergrund weiter; ein Fehler kommt danach als Hinweis
  const start = (r: Result) => {
    const title = books.find(b => b.id === bookId)?.title ?? r.title
    onClose()
    api(`/books/${bookId}/${base}grab`, { method: 'POST', body: JSON.stringify(r) })
      .then(() => onStarted())
      .catch(e => { void tell(t('«{{v0}}» liess sich nicht starten: {{v1}}', { v0: title, v1: (e as Error).message })) })
  }
  // Ausblenden gilt einen Monat für diesen Band; die Liste ändert sich sofort, ohne neue Suche
  const toggleHide = async (r: Result) => {
    setActionError(null)
    try {
      const res = await api<{ until?: string }>(`/books/${bookId}/${r.hidden_until ? 'unhide' : 'hide'}`, { method: 'POST', body: JSON.stringify({ guid: r.guid }) })
      setData(d => d && { ...d, results: d.results.map(x => x.guid === r.guid ? { ...x, hidden_until: res.until } : x) })
    } catch (e) { setActionError((e as Error).message) }
  }
  const dismiss = async () => {
    setActionError(null)
    try { await api(`/books/${bookId}/dismiss`, { method: 'POST' }); onStarted(); onClose() } catch (e) { setActionError((e as Error).message) }
  }
  const book = books.find(b => b.id === bookId)
  const info: BookInfo | undefined = data?.book ?? (book && { id: book.id, title: book.title, author: book.author ?? null, narrator: book.narrator ?? null,
    sequence: book.sequence, series: null, lang: null, release_date: book.release_date ?? null, runtime_min: book.runtime_min ?? null })
  // E-Books erscheinen oft vor dem Hörbuch: dort gilt der Datumsfilter nicht
  const dateFilter = !ebook
  const words = fold(text).split(' ').filter(Boolean)
  const shown = data ? data.results.filter(r => {
    if (r.hidden_until && !showHidden) return false
    if (words.length) { const tx = fold(r.title); if (!words.every(w => tx.includes(w))) return false }
    return !(dateFilter && hideOld && tooOld(r, info, data.searched_at))
  }) : []
  const filtered = data ? data.results.length - shown.length : 0
  const hiddenByMe = data ? data.results.filter(r => r.hidden_until).length : 0
  const old = data && dateFilter ? data.results.filter(r => tooOld(r, info, data.searched_at)).length : 0
  const hidden = shown.filter(r => r.verdict === 'verworfen').length
  return (
    <dialog ref={ref} onClose={onClose} onClick={e => e.target === ref.current && onClose()}
      className="m-auto max-h-[90dvh] w-[min(760px,calc(100vw-24px))] rounded-2xl border border-line bg-bg p-0 text-text shadow-2xl backdrop:bg-black/70">
      <div className="flex max-h-[90dvh] flex-col">
        <header className="flex items-start gap-3 border-b border-line p-4">
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-semibold">{ebook ? t('EPUB von Hand wählen') : t('Geführte Suche')}</h2>
            {info && (
              <p className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted">
                <span className="font-medium text-text">{info.title}</span>
                {info.author && <span>{info.author}</span>}
                {info.series && <span>{info.series}{info.sequence ? t(' · Band {{v0}}', { v0: info.sequence }) : ''}</span>}
                {info.narrator && <span>{t('gelesen von')}{' '}{info.narrator}</span>}
                {info.lang && <span>{LANG[info.lang] ?? info.lang}</span>}
                {info.release_date && <span>{t('erschienen')}{' '}{fmtDate(info.release_date)}</span>}
              </p>
            )}
            <label htmlFor="search-book" className="sr-only">{t('Band auswählen')}</label>
            <select id="search-book" value={bookId} onChange={e => setBookId(Number(e.target.value))}
              className="mt-2 h-10 w-full rounded-lg border border-line bg-surface px-3 text-sm">
              {books.map(b => <option key={b.id} value={b.id}>{b.sequence ? t('Band {{v0}}: ', { v0: b.sequence }) : ''}{b.title}</option>)}
            </select>
          </div>
          <button type="button" onClick={onClose} aria-label={t('Suche schliessen')} className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-muted hover:bg-surface hover:text-text"><X size={18} aria-hidden /></button>
        </header>
        <div className="flex-1 overflow-y-auto p-4">
          {data && (
            <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
              <span className="text-muted">{t('Quellen:')}</span>
              {data.sources.map(s => (
                <span key={s.key} className={`rounded-full px-2.5 py-1 font-semibold ${s.active ? 'bg-surface-2 text-text' : 'border border-dashed border-line text-faint'}`}
                  title={s.active ? t('Durchsucht') : ebook ? t('Nicht eingerichtet') : t('Für diesen Band nicht durchsucht (Quelle passt nicht zur Sprache oder ist aus)')}>{s.name}{s.active ? '' : t(' (nicht durchsucht)')}</span>
              ))}
              <button type="button" onClick={() => setNonce(nonce + 1)} className="ml-auto inline-flex min-h-9 items-center gap-1.5 font-medium text-muted hover:text-text"><RefreshCw size={13} aria-hidden />{t('Neu suchen')}</button>
            </div>
          )}
          {error && <div className="mb-3"><ErrorBox message={error} /></div>}
          {data?.blocked && <p className="mb-3 text-sm" style={{ color: 'var(--warn)' }}>{t('Kein Download möglich:')}{' '}{data.blocked}.</p>}
          {ebook && data && !data.blocked && data.results.length > 0 && <p className="mb-3 text-sm text-muted">{t('Deine Wahl gilt: liegt im Download genau ein gültiges EPUB, legt ABW es in den Buchordner, auch wenn Titel oder Bandnummer nicht sicher passen.')}</p>}
          {actionError && <div className="mb-3"><ErrorBox plain message={actionError} /></div>}
          {!data && !error && (
            <div className="space-y-3" aria-busy="true" aria-label={t('Sucht in den Quellen')}>
              <p className="flex items-center gap-2 text-sm text-muted"><Loader2 size={15} className="animate-spin" aria-hidden />{ebook ? t('Sucht') : nonce > 0 ? t('Sucht') : t('Lädt die gespeicherte Suche oder sucht')} {t('in den Quellen nach «')}{book?.title}». {ebook ? t('EBookBay und das Internet Archive brauchen oft mehrere Minuten.') : t('Eine frische Suche dauert je nach Quelle bis zu drei Minuten.')}</p>
              {[0, 1, 2].map(i => <div key={i} className="skeleton h-24 rounded-xl" />)}
            </div>
          )}
          {data && (data.paused?.length ?? 0) > 0 && <p className="mb-3 text-sm" style={{ color: 'var(--warn)' }}>{t('Nicht gefragt, weil Prowlarr sie wegen des API-Limits gesperrt hat:')}{' '}{data.paused!.map(p => t('{{v0}} (bis {{v1}})', { v0: p.indexer, v1: new Date(p.until).toLocaleString(locale(), { weekday: 'short', hour: '2-digit', minute: '2-digit' }) })).join(', ')}.{ebook ? '' : t(' Die Automatik sucht nach Ende der Sperre erneut.')}</p>}
          {data && data.errors.length > 0 && <p className="mb-3 text-sm" style={{ color: 'var(--warn)' }}>{t('Ein Teil der Suche ist fehlgeschlagen:')}{' '}{data.errors.join('; ')}</p>}
          {data && data.results.length === 0 && (
            <div className="rounded-xl border border-dashed border-line px-4 py-10 text-center">
              <p className="font-semibold">{t('Kein Treffer in den Quellen')}</p>
              <p className="mx-auto mt-1 max-w-md text-sm text-muted">{t('Gesucht wurde nach:')}{' '}{data.queries.join(' · ')}.{ebook ? '' : t(' Die Automatik versucht es später erneut, sobald sie eingeschaltet ist.')}</p>
            </div>
          )}
          {data && data.results.length > 0 && (
            <>
              {data.cached && <p className="mb-2 text-sm text-muted">{t('Gespeicherte Suche vom')}{' '}{fmtDateTime(data.searched_at)}{t('. Mit «Neu suchen» fragst du die Quellen frisch ab.')}</p>}
              <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2">
                <label className="relative min-w-0 flex-1 basis-56">
                  <span className="sr-only">{t('Treffer filtern')}</span>
                  <Search size={14} aria-hidden className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
                  <input type="search" value={text} onChange={e => setText(e.target.value)} placeholder={t('Treffer filtern, z. B. Sprecher oder Format')}
                    className="h-9 w-full rounded-lg border border-line bg-surface pl-8 pr-3 text-sm" />
                </label>
                {dateFilter && (
                  <label className="inline-flex min-h-9 items-center gap-2 text-sm text-muted" title={info?.release_date ? t('Erschienen {{v0}}. Treffer ohne Datum bleiben sichtbar.', { v0: fmtDate(info.release_date) }) : t('Erscheinungsdatum unbekannt, der Filter wirkt nicht')}>
                    <input type="checkbox" checked={hideOld} onChange={e => setHideOld(e.target.checked)} className="h-4 w-4 accent-[var(--accent)]" />
                    {t('Älter als das Buch ausblenden')}{old ? t(' ({{v0}})', { v0: old }) : ''}
                  </label>
                )}
                {(hiddenByMe > 0 || showHidden) && (
                  <label className="inline-flex min-h-9 items-center gap-2 text-sm text-muted">
                    <input type="checkbox" checked={showHidden} onChange={e => setShowHidden(e.target.checked)} className="h-4 w-4 accent-[var(--accent)]" />
                    {t('Ausgeblendete zeigen (')}{hiddenByMe})
                  </label>
                )}
              </div>
              <p className="mb-3 text-sm text-muted">{shown.length} {t('Treffer, beste zuerst')}{filtered ? t(' ({{v0}} ausgeblendet)', { v0: filtered }) : ''}{hidden ? t(', davon {{v0}} verworfen (bleiben sichtbar und lassen sich trotzdem starten)', { v0: hidden }) : ''}.</p>
              {shown.length === 0
                ? <p className="rounded-xl border border-dashed border-line px-4 py-8 text-center text-sm text-muted">{t('Kein Treffer passt zu den Filtern.')}</p>
                : <ul className="space-y-2.5">
                  {shown.map(r => <ResultRow key={r.guid} r={r} canStart={data.can_start} onStart={() => start(r)} onHide={() => toggleHide(r)} />)}
                </ul>}
            </>
          )}
        </div>
        {data && !ebook && (data.listed || data.results.some(r => r.verdict === 'unsicher')) && (
          <footer className="flex items-center justify-end gap-3 border-t border-line px-4 py-2">
            <span className="hidden text-xs text-muted sm:inline">{data.results.some(r => r.verdict === 'unsicher') ? t('Unsichere Treffer bekommen 5 Punkte Abzug') : t('Nimmt den Band aus «Treffer wählen»')}{t('; er wird weiter gesucht.')}</span>
            <button type="button" onClick={dismiss} title={t('Die unsicheren Treffer gelten als abgelehnt und bekommen bei diesem Band 5 Punkte Abzug. ABW sucht den Band weiter und meldet sich erst bei einem neuen Treffer wieder.')}
              className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-line px-3 text-sm font-semibold hover:border-faint">
              <Ban size={14} aria-hidden />{t('Kein Treffer passt')}
            </button>
          </footer>
        )}
      </div>
    </dialog>
  )
}
