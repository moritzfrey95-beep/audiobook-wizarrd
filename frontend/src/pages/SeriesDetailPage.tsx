import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { AlertTriangle, ArrowLeft, BookOpen, FileAudio, Check, CheckCircle2, ChevronDown, ChevronUp, CircleHelp, ExternalLink, Eye, EyeOff, Headphones, Link2, ListFilter, Loader2, RefreshCw, Search, SkipForward, Unlink, WandSparkles, X } from 'lucide-react'
import { api, fmtDate, fmtRuntime, useData, type Book, type CanMark, type Candidate, type Edition, type Lang, type SeriesDetail } from '../api'
import { Badge, Button, Cover, ErrorBox, LangTag, Page, Section, ask, shownFlags, tell } from '../ui'
import { SearchDialog } from '../SearchDialog'
import { Switch } from '../Automation'
import { FollowButton } from '../Follow'
import { useAdmin } from '../session'
import { StarText, Stars } from '../Stars'
import { ToReadButton, ToReadKeys } from '../ToRead'
import { locale, t } from '../i18n'

const SEARCHABLE = ['missing', 'searching', 'failed']
const DL_TEXT: Record<string, string> = {
  arrived: t('Heruntergeladen, die Ankunftsprüfung läuft gleich.'),
  verified: t('Prüfung bestanden, bereit für die Ablage.'),
  review: t('Prüfung mit Hinweisen, bitte ansehen.'),
  imported: t('Abgelegt, wartet auf Audiobookshelf.'),
}

const LANG_NAME: Record<Lang, string> = { en: t('Englisch'), de: t('Deutsch') }

async function convertBook(b: Book) {
  const p = await api<{ files: number; bytes: number; target: string; problems: string[] }>(`/books/${b.id}/convert`)
  if (p.problems.length) { await tell(t('Umwandlung nicht möglich: {{v0}}', { v0: p.problems.join('; ') })); return }
  if (!await ask(t('«{{v0}}» umwandeln?\n\n{{v1}} Dateien ({{v2}} MB) werden zu «{{v3}}» zusammengefasst. Die M4B wird gegen die Originale geprüft; erst danach gehen die Originale in den Papierkorb.', { v0: b.title, v1: p.files, v2: Math.round(p.bytes / 1e6), v3: p.target }), t('Umwandeln'))) return
  await api(`/books/${b.id}/convert`, { method: 'POST' })
  await tell(t('Die Umwandlung läuft im Hintergrund. Den Fortschritt siehst du unter Aufgaben, «Umwandlung und Kapitel».'))
}

/** EPUB zum vorhandenen Hörbuch: Kennzeichen, laufender Download oder Knopf für die Suche auf Usenet. */
function EbookControl({ b, onChange }: { b: Book; onChange: () => void }) {
  const [busy, setBusy] = useState(false)
  const [pick, setPick] = useState(false)
  const [res, setRes] = useState<{ started: boolean; message: string } | null>(null)
  const chip = 'inline-flex items-center gap-1 rounded-full bg-surface-2 px-2.5 py-1 text-xs font-medium text-muted'
  const admin = useAdmin()
  if (b.epub) return <span className={chip} title={b.state === 'owned' ? t('Im Buchordner liegt ein E-Book (EPUB)') : t('Das E-Book (EPUB) liegt schon im künftigen Buchordner; das Hörbuch folgt, sobald es erschienen ist')}><BookOpen size={12} aria-hidden />{b.state === 'owned' ? 'EPUB' : t('EPUB vorab')}</span>
  if (b.state !== 'owned' || !admin) return null
  if (b.ebook?.state === 'loading' || res?.started) return <span className={chip} title={res?.message ?? t('Das EPUB wird über Usenet geladen, geprüft und in den Buchordner gelegt')}><Loader2 size={12} aria-hidden className="animate-spin" />{t('EPUB lädt')}</span>
  const last = res?.message ?? b.ebook?.message
  const find = async () => {
    setBusy(true)
    try { setRes(await api<{ started: boolean; message: string }>(`/books/${b.id}/ebook`, { method: 'POST' })) }
    catch (e) { setRes({ started: false, message: (e as Error).message }) }
    finally { setBusy(false) }
  }
  return (
    <>
      <button type="button" onClick={find} disabled={busy} title={last ? t('Letzte Suche: {{v0}}', { v0: last }) : t('Sucht das EPUB zu diesem Hörbuch (Usenet, sonst EBookBay und Internet Archive) und legt es in den Buchordner')}
        className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-line px-3 text-xs font-semibold hover:border-accent hover:text-accent disabled:opacity-60">
        {busy ? <Loader2 size={13} aria-hidden className="animate-spin" /> : <BookOpen size={13} aria-hidden />}{busy ? t('Sucht EPUB…') : t('EPUB suchen')}
      </button>
      <button type="button" onClick={() => setPick(true)} title={t('Zeigt alle EPUB-Treffer aller Quellen, auch die unsicheren, zum Auswählen von Hand')}
        className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-line px-3 text-xs font-semibold hover:border-accent hover:text-accent">
        <ListFilter size={13} aria-hidden />{t('EPUB wählen')}
      </button>
      {res && !res.started && <span role="status" className="basis-full text-xs text-muted">{res.message}</span>}
      {pick && <SearchDialog kind="ebook" books={[b]} initial={b.id} onClose={() => setPick(false)} onStarted={onChange} />}
    </>
  )
}

/** Als gehört markieren oder zurücknehmen. Wird nach Audiobookshelf übertragen; wer dort keine
 * Sitzung hat, bekommt den Hinweis, sich mit dem Audiobookshelf-Passwort neu anzumelden. */
function ListenedButton({ b, canMark, onChange }: { b: Book; canMark?: CanMark; onChange: () => void }) {
  const [busy, setBusy] = useState(false)
  if (b.state !== 'owned' || !b.abs_item_id || !canMark) return null
  const toggle = async () => {
    if (!canMark.can) { await tell(canMark.why ?? t('Markieren geht für dein Konto nicht.')); return }
    if (b.finished && !await ask(t('«{{v0}}» wieder als nicht gehört markieren? In Audiobookshelf steht das Buch dann wieder an der Stelle, an der du vor dem Markieren warst, sonst am Anfang.', { v0: b.title }), t('Nicht gehört'))) return
    setBusy(true)
    try { await api(`/books/${b.id}/listened`, { method: 'POST', body: JSON.stringify({ finished: !b.finished }) }); onChange() }
    catch (e) { await tell((e as Error).message) }
    finally { setBusy(false) }
  }
  return (
    <button type="button" onClick={toggle} disabled={busy} title={b.finished ? t('Markierung zurücknehmen, auch in Audiobookshelf') : t('Als gehört markieren, auch in Audiobookshelf')}
      className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-line px-3 text-xs font-semibold hover:border-accent hover:text-accent disabled:opacity-60">
      {busy ? <Loader2 size={13} aria-hidden className="animate-spin" /> : b.finished ? <X size={13} aria-hidden /> : <CheckCircle2 size={13} aria-hidden />}
      {b.finished ? t('Nicht gehört') : t('Als gehört markieren')}
    </button>
  )
}

/** Sterne am Band: eigene zum Ändern, sobald gehört; die der anderen bei mehreren zusammengefasst
 * (Schnitt und Anzahl) und ausklappbar. */
function BookRating({ b, onChange }: { b: Book; onChange: () => void }) {
  const [open, setOpen] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const others = b.rating?.others ?? []
  if (!b.finished && !others.length) return null
  const rate = async (stars: number) => {
    setErr(null)
    try { await api(`/ratings/book/${b.id}`, { method: 'POST', body: JSON.stringify({ stars }) }); onChange() } catch (e) { setErr((e as Error).message) }
  }
  const avg = others.length ? others.reduce((a, o) => a + o.stars, 0) / others.length : 0
  return (
    <div className="flex flex-col gap-0.5 text-xs text-muted">
      {b.finished && (
        <div className="flex flex-wrap items-center gap-x-2">
          <span>{t('Deine Bewertung')}</span>
          <Stars small value={b.rating?.mine} onPick={rate} label={`${b.title} bewerten`} />
          {b.rating?.skipped && <span className="text-faint">{t('(nicht bewertet)')}</span>}
        </div>
      )}
      {others.length === 1 && <p>{others[0].name}: <StarText n={others[0].stars} /></p>}
      {others.length > 1 && (
        <>
          <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open}
            className="inline-flex min-h-8 items-center gap-1 self-start hover:text-text">
            {open ? <ChevronUp size={13} aria-hidden /> : <ChevronDown size={13} aria-hidden />}
            {t('Andere:')}{' '}<StarText n={avg} /> <span className="tabular">{avg.toLocaleString(locale(), { maximumFractionDigits: 1 })}</span> {t('aus')}{' '}{others.length}
          </button>
          {open && (
            <ul className="ml-4 flex flex-col gap-0.5">
              {others.map(o => <li key={o.name}>{o.name}: <StarText n={o.stars} /></li>)}
            </ul>
          )}
        </>
      )}
      {err && <p style={{ color: 'var(--bad)' }}>{err}</p>}
    </div>
  )
}

/** Ein Band mit Status und Knöpfen; auch auf der Seite «Autor – Einzelbücher» (note = Zeile wie «Hinzugefügt von …»). */
export function BookCard({ b, absUrl, lang, canMark, onSearch, onChange, note }: { b: Book; absUrl: string; lang: Lang; canMark?: CanMark; onSearch: (id: number) => void; onChange: () => void; note?: string }) {
  const label = b.state === 'upcoming' ? (b.release_date ? t('Erscheint {{v0}}', { v0: fmtDate(b.release_date) }) : t('Angekündigt, Termin offen')) : undefined
  const admin = useAdmin()
  const [autoRun, setAutoRun] = useState(false)
  const autoSt = useData<{ running: boolean; result: string | null; text: string }>(autoRun ? `/books/${b.id}/autosearch` : null, 2000).data
  const autoBusy = autoRun && (!autoSt || autoSt.running)
  const startAuto = async () => { await api(`/books/${b.id}/autosearch`, { method: 'POST' }); setAutoRun(true) }
  const link = b.abs_item_id && absUrl ? `${absUrl}/item/${b.abs_item_id}` : b.asin ? `https://www.audible.${lang === 'de' ? 'de' : 'com'}/pd/${b.asin}` : null
  return (
    <li className={`flex gap-3 rounded-xl border border-line bg-surface p-3 ${b.state === 'owned' ? '' : 'border-dashed'}`}>
      <Cover src={b.cover} alt={b.title} className={`h-24 w-24 shrink-0 rounded-lg ${b.state === 'owned' ? '' : 'opacity-70 saturate-50'}`} />
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wide text-faint">{b.sequence ? t('Band {{v0}}', { v0: b.sequence }) : note !== undefined ? t('Einzelbuch') : t('Ohne Bandnummer')}</p>
            <h3 title={b.full_title !== b.title ? b.full_title : undefined}
              className="line-clamp-2 font-[family-name:var(--font-sans)] text-[15px] font-semibold leading-snug tracking-normal">{b.title}</h3>
          </div>
          {link && (
            <a href={link} target="_blank" rel="noreferrer" aria-label={t('{{v0}} {{v1}} öffnen', { v0: b.title, v1: b.abs_item_id ? t('in Audiobookshelf') : t('bei Audible') })}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded text-faint hover:text-text"><ExternalLink size={15} aria-hidden /></a>
          )}
        </div>
        <p className="truncate text-xs text-muted">
          {fmtRuntime(b.runtime_min)}{b.narrator ? t(' · gelesen von {{v0}}', { v0: b.narrator }) : ''}
        </p>
        {b.also?.length > 0 && (
          <p className="text-xs text-faint" title={b.also.map(o => o.title).join('\n')}>
            {b.also.some(o => o.owned) && b.state === 'owned'
              ? t('{{v0}} Ausgaben im Bestand', { v0: b.also.filter(o => o.owned).length + 1 })
              : b.also.length === 1 ? t('Weitere Ausgabe: {{v0}}', { v0: b.also[0].title }) : t('{{v0}} weitere Ausgaben', { v0: b.also.length })}
          </p>
        )}
        {!b.finished && (b.progress ?? 0) > 0.01 && (
          <div className="h-1.5 overflow-hidden rounded-full bg-surface-2" role="progressbar" aria-label={t('Dein Hörfortschritt')}
            aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((b.progress ?? 0) * 100)}>
            <div className="h-full rounded-full" style={{ width: `${Math.round((b.progress ?? 0) * 100)}%`, background: 'var(--accent)' }} />
          </div>
        )}
        {note && <p className="text-xs text-faint">{note}</p>}
        <BookRating b={b} onChange={onChange} />
        <div className="mt-auto flex flex-wrap items-center gap-1.5">
          <Badge status={b.state} label={label} />
          {b.finished && <span className="inline-flex items-center gap-1 rounded-full bg-surface-2 px-2.5 py-1 text-xs font-medium text-muted"><Check size={12} aria-hidden />{t('Gehört')}</span>}
          {!b.finished && (b.progress ?? 0) > 0.01 && (
            <span className="inline-flex items-center gap-1 rounded-full bg-surface-2 px-2.5 py-1 text-xs font-medium text-muted"><Headphones size={12} aria-hidden />{Math.round((b.progress ?? 0) * 100)} {t('% gehört')}</span>
          )}
          <ListenedButton b={b} canMark={canMark} onChange={onChange} />
          {!b.sequence && !b.finished && <ToReadButton kind="book" id={b.id} title={b.title} />}
          {SEARCHABLE.includes(b.state) && (admin ? (
            <button type="button" onClick={() => onSearch(b.id)}
              className="ml-auto inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-line px-3 text-xs font-semibold hover:border-accent hover:text-accent">
              <Search size={13} aria-hidden />{t('Suchen')}
            </button>
          ) : (
            <button type="button" onClick={startAuto} disabled={autoBusy} title={t('Sucht in allen Quellen und lädt einen sicheren Treffer sofort')}
              className="ml-auto inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-line px-3 text-xs font-semibold hover:border-accent hover:text-accent disabled:opacity-60">
              {autoBusy ? <Loader2 size={13} aria-hidden className="animate-spin" /> : <WandSparkles size={13} aria-hidden />}{autoBusy ? t('Sucht…') : t('Automatisch suchen')}
            </button>
          ))}
          <EbookControl b={b} onChange={onChange} />
          {admin && b.state === 'owned' && (b.num_files ?? 0) > 1 && (
            <button type="button" onClick={() => convertBook(b)}
              className="ml-auto inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-line px-3 text-xs font-semibold hover:border-accent hover:text-accent">
              <FileAudio size={13} aria-hidden />{t('In M4B umwandeln')}
            </button>
          )}
        </div>
        {autoRun && autoSt && !autoSt.running && !b.download && <p role="status" className="text-xs text-muted">{autoSt.text}</p>}
        {b.download && (
          <div className="mt-1" title={b.download.release_title}>
            {!['queued', 'downloading'].includes(b.download.state)
              ? <p className="text-xs text-muted">{DL_TEXT[b.download.state] ?? t('In Arbeit.')} {admin && <Link to="/pruefen" className="text-accent underline-offset-4 hover:underline">{t('Zum Tab «Prüfen»')}</Link>}</p>
              : (
                <div className="flex items-center gap-2 text-xs text-muted">
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-2"><div className="h-full rounded-full transition-[width] duration-500" style={{ width: `${Math.round((b.download.progress ?? 0) * 100)}%`, background: 'var(--load)' }} /></div>
                  <span className="tabular w-10 text-right">{Math.round((b.download.progress ?? 0) * 100)} %</span>
                </div>
              )}
          </div>
        )}
      </div>
    </li>
  )
}

function EditionPanel({ e, sid, absUrl, canUnlink, canMark, onChange, onSearch }: { e: Edition; sid: number; absUrl: string; canUnlink: boolean; canMark?: CanMark; onChange: () => void; onSearch: (id: number) => void }) {
  const [busy, setBusy] = useState(false)
  const admin = useAdmin()
  const [filter, setFilter] = useState<'all' | 'open'>('all')
  const books = (e.books ?? []).filter(b => filter === 'all' || b.state !== 'owned')
  const toggle = async () => {
    setBusy(true)
    try { await api(`/series/${sid}/monitor`, { method: 'POST', body: JSON.stringify({ edition_id: e.id, monitored: !e.monitored }) }); onChange() } finally { setBusy(false) }
  }
  const unlink = async () => {
    const text = e.extra
      ? t('Die Fassung «{{v0}}» ist hier nicht richtig zugeordnet? Sie wird gelöst und nicht wieder automatisch zugeordnet. Bücher werden dabei nicht verändert.', { v0: e.variant })
      : t('Die Fassung «{{v0}}» von dieser Serie trennen? Bücher werden dabei nicht verändert.', { v0: e.name })
    if (!await ask(text, t('Trennen'))) return
    await api(`/series/${sid}/unlink/${e.id}`, { method: 'POST' }); onChange()
  }
  return (
    <section className="mt-8">
      <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-2">
        <LangTag lang={e.lang} />
        <h2 className="text-lg font-semibold">{e.variant ? t('{{v0}} · {{v1}}', { v0: LANG_NAME[e.lang], v1: e.variant }) : e.name}</h2>
        {e.variant && <span className="text-sm text-faint" title={t('Serie in Audiobookshelf')}>{e.name}</span>}
        <span className="tabular text-sm text-muted">
          {e.owned} {t('von')}{' '}{e.audible_asin ? e.total : '?'} {t('vorhanden')}{e.upcoming ? t(', {{v0}} angekündigt', { v0: e.upcoming }) : ''}
        </span>
        {shownFlags(e.flags).map(f => <Badge key={f} status={f} />)}
        {e.link_source === 'auto-titel' && <span className="rounded-full bg-surface-2 px-2.5 py-1 text-xs text-muted" title={t('ABW hat diese Fassung trotz anderem Titel selbst zugeordnet: einzige offene Serie des Autors, erster Band ähnlich lang. Falsch? Dann «Trennen».')}>{t('automatisch zugeordnet')}</span>}
        <div className="ml-auto flex flex-wrap gap-2">
          {(e.books ?? []).some(b => b.sequence && !/[-–]/.test(b.sequence)) && <ToReadButton kind="edition" id={e.id} title={e.name} />}
          <Button onClick={() => setFilter(filter === 'all' ? 'open' : 'all')}>
            <ListFilter size={15} aria-hidden />{filter === 'all' ? t('Nur offene Bände') : t('Alle Bände')}
          </Button>
          {admin && (
            <Button onClick={toggle} disabled={busy}>
              {e.monitored ? <EyeOff size={15} aria-hidden /> : <Eye size={15} aria-hidden />}{e.monitored ? t('Überwachung beenden') : t('Überwachen')}
            </Button>
          )}
          {admin && canUnlink && <Button onClick={unlink} title={e.extra ? t('Diese Fassung gehört nicht zu dieser Serie oder ist falsch aufgeteilt') : t('Diese Sprachfassung gehört nicht zu dieser Serie')}><Unlink size={15} aria-hidden />{t('Trennen')}</Button>}
        </div>
      </div>
      {e.sync_error && (
        <p className="mb-3 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-muted">
          {e.flags.includes('single') || e.flags.includes('absent')
            ? <>{e.sync_error}{t('. Es gibt darum keine fehlenden oder angekündigten Bände; ABW prüft das bei jedem Serienabgleich erneut.')}</>
            : <>{t('Für diese Fassung liegt kein Audible-Abgleich vor, fehlende und angekündigte Bände sind darum unbekannt. Grund:')}{' '}{e.sync_error}</>}
        </p>
      )}
      {books.length === 0
        ? <p className="rounded-xl border border-dashed border-line px-4 py-8 text-center text-sm text-muted">{filter === 'open' ? t('Alle Bände dieser Fassung sind vorhanden.') : t('Zu dieser Fassung sind noch keine Bände bekannt.')}</p>
        : <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">{books.map(b => <BookCard key={b.id} b={b} absUrl={absUrl} lang={e.lang} canMark={canMark} onSearch={onSearch} onChange={onChange} />)}</ul>}
    </section>
  )
}

function Candidates({ lang, items, sid, onChange }: { lang: Lang; items: Candidate[]; sid: number; onChange: () => void }) {
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const link = async (c: Candidate) => {
    setBusy(c.series_asin); setErr(null)
    try { await api(`/series/${sid}/link`, { method: 'POST', body: JSON.stringify({ lang, series_asin: c.series_asin, title: c.series_title }) }); onChange() }
    catch (e) { setErr(t('Die Verknüpfung hat nicht geklappt. {{v0}}', { v0: (e as Error).message })) } finally { setBusy(null) }
  }
  return (
    <Section title={t('{{v0}}e Fassung', { v0: LANG_NAME[lang] })} hint={t('Noch nicht verknüpft')}>
      {items.length === 0 ? (
        <p className="rounded-xl border border-dashed border-line px-4 py-6 text-sm text-muted">
          {t('Bei Audible ist unter diesem Autor keine')}{' '}{LANG_NAME[lang].toLowerCase()}{t('e Serie gefunden worden. Der Autorenkatalog wird täglich neu geprüft.')}
        </p>
      ) : (
        <>
          <p className="mb-3 text-sm text-muted">{t('Übersetzte Serien heissen oft anders. Wähle die passende Serie desselben Autors, dann erscheint sie hier als zweite Sprache.')}</p>
          {err && <p role="alert" className="mb-3 text-sm" style={{ color: 'var(--bad)' }}>{err}</p>}
          <ul className="flex gap-3 overflow-x-auto pb-2">
            {items.map(c => (
              <li key={c.series_asin} className="w-40 shrink-0 overflow-hidden rounded-xl border border-line bg-surface">
                <Cover src={c.cover_url} alt={c.series_title} />
                <div className="space-y-2 p-2.5">
                  <p className="line-clamp-2 min-h-10 text-sm font-semibold leading-snug">{c.series_title}</p>
                  <p className="text-xs text-muted">{c.book_count} {c.book_count === 1 ? t('Band') : t('Bände')}</p>
                  {c.likely && <p className="text-xs font-semibold" style={{ color: 'var(--ok)' }} title={t('Die einzige Serie dieses Autors, die in beiden Sprachen noch keine zweite Fassung hat')}>{t('Passt wahrscheinlich')}</p>}
                  <button type="button" onClick={() => link(c)} disabled={busy !== null}
                    className="flex min-h-9 w-full items-center justify-center gap-1.5 rounded-lg border border-line text-xs font-semibold hover:border-accent hover:text-accent disabled:opacity-50">
                    <Link2 size={13} aria-hidden />{busy === c.series_asin ? t('Verknüpft…') : t('Verknüpfen')}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </Section>
  )
}

interface AutoItem { book_id: number; title: string; sequence: string | null; result: 'started' | 'unsure' | 'none' | 'skipped' | 'error'; text: string }
interface AutoState { running: boolean; total: number; done: number; items: AutoItem[] }

const AUTO_ICON = {
  started: { icon: CheckCircle2, color: 'var(--ok)' }, unsure: { icon: CircleHelp, color: 'var(--warn)' },
  none: { icon: X, color: 'var(--faint)' }, skipped: { icon: SkipForward, color: 'var(--faint)' }, error: { icon: AlertTriangle, color: 'var(--bad)' },
}

function AutoPanel({ state, onClose, onPick, canPick }: { state: AutoState; onClose: () => void; onPick: (id: number) => void; canPick: boolean }) {
  const started = state.items.filter(i => i.result === 'started').length
  return (
    <section className="mt-6 rounded-xl border border-line bg-surface p-4" aria-live="polite">
      <div className="flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-base font-semibold">
          {state.running && <Loader2 size={16} className="animate-spin" aria-hidden />}
          {state.running ? t('Automatische Suche läuft: {{v0}} von {{v1}} Bänden', { v0: state.done, v1: state.total || '…' }) : t('Automatische Suche beendet: {{v0}} von {{v1}} Bänden gestartet', { v0: started, v1: state.total })}
        </h2>
        <button type="button" onClick={onClose} aria-label={t('Ergebnis der automatischen Suche schliessen')} className="flex h-9 w-9 items-center justify-center rounded-lg text-muted hover:bg-surface-2 hover:text-text"><X size={16} aria-hidden /></button>
      </div>
      {state.total > 0 && <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-surface-2"><div className="h-full rounded-full transition-[width] duration-500" style={{ width: `${(state.done / state.total) * 100}%`, background: 'var(--accent)' }} /></div>}
      <ul className="mt-3 space-y-1.5 text-sm">
        {state.items.map(i => {
          const Icon = AUTO_ICON[i.result].icon
          return (
            <li key={i.book_id} className="flex flex-wrap items-start gap-x-2 gap-y-1">
              <Icon size={15} aria-hidden className="mt-0.5 shrink-0" style={{ color: AUTO_ICON[i.result].color }} />
              <span className="font-medium">{i.sequence ? t('Band {{v0}}: ', { v0: i.sequence }) : ''}{i.title}</span>
              <span className="min-w-0 break-words text-muted">{i.text}</span>
              {canPick && i.result === 'unsure' && <button type="button" onClick={() => onPick(i.book_id)} className="text-accent underline-offset-4 hover:underline">{t('Treffer ansehen')}</button>}
            </li>
          )
        })}
      </ul>
    </section>
  )
}

export default function SeriesDetailPage() {
  const { id } = useParams()
  const { data: s, error, reload } = useData<SeriesDetail>(`/series/${id}`, 30000)
  const toread = useData<string[]>('/toread/keys')
  const [refreshing, setRefreshing] = useState(false)
  const [ebookMsg, setEbookMsg] = useState<string | null>(null)
  const [params] = useSearchParams()
  const [searchBook, setSearchBook] = useState<number | null>(() => Number(params.get('suche')) || null)
  const admin = useAdmin()
  const [autoOpen, setAutoOpen] = useState(false)
  const auto = useData<AutoState>(autoOpen ? `/series/${id}/autosearch` : null, 2000)
  if (error && !s) return <Page title={t('Serie')}><ErrorBox message={error} onRetry={reload} /></Page>
  if (!s) return <Page title={t('Serie wird geladen')}><div className="skeleton h-56 rounded-2xl" /></Page>
  const open = s.editions.filter(e => e.monitored).flatMap(e => e.books ?? []).filter(b => SEARCHABLE.includes(b.state))
  const startAuto = async () => { await api(`/series/${s.id}/autosearch`, { method: 'POST' }); setAutoOpen(true); auto.reload() }
  const findEbooks = async () => {
    try {
      const r = await api<{ total: number }>(`/series/${s.id}/ebooks`, { method: 'POST' })
      setEbookMsg(r.total ? t('EPUB-Suche läuft im Hintergrund für {{v0}} {{v1}}. Sichere Treffer werden geladen, geprüft und in den Buchordner gelegt.', { v0: r.total, v1: r.total === 1 ? t('Band') : t('Bände') }) : t('Alle vorhandenen Bände haben schon ein EPUB oder es lädt gerade.'))
    } catch (e) { setEbookMsg((e as Error).message) }
  }
  const refresh = async () => { setRefreshing(true); try { await api(`/series/${s.id}/refresh`, { method: 'POST' }); reload() } finally { setRefreshing(false) } }
  return (
    <main className="mx-auto w-full max-w-[1600px] px-4 pb-16 pt-5 sm:px-6 lg:px-8">
      <Link to="/" className="inline-flex min-h-9 items-center gap-1.5 text-sm font-medium text-muted hover:text-text"><ArrowLeft size={15} aria-hidden />{t('Alle Serien')}</Link>
      <div className="relative mt-3 overflow-hidden rounded-2xl border border-line bg-surface">
        {s.cover && <img src={s.cover} alt="" aria-hidden className="absolute inset-0 h-full w-full scale-125 object-cover opacity-20 blur-3xl" />}
        <div className="relative flex flex-col gap-5 p-5 sm:flex-row sm:p-6">
          <Cover src={s.cover} alt={s.name} className="w-40 shrink-0 rounded-xl shadow-[var(--shadow)] sm:w-52" />
          <div className="flex min-w-0 flex-1 flex-col gap-3">
            <div className="flex flex-wrap gap-1.5">{shownFlags(s.flags).map(f => <Badge key={f} status={f} />)}</div>
            <div>
              <h1 className="text-3xl font-bold sm:text-4xl">{s.name}</h1>
              <Link to={`/autor/${s.author_key}`} className="mt-1 inline-block text-muted underline-offset-4 hover:text-text hover:underline">{s.author}</Link>
            </div>
            <dl className="flex flex-wrap gap-x-8 gap-y-2 text-sm">
              {s.editions.map(e => (
                <div key={e.id}><dt className="text-faint">{LANG_NAME[e.lang]}{e.variant ? t(' · {{v0}}', { v0: e.variant }) : ''}</dt><dd className="tabular font-semibold">{e.owned} {t('von')}{' '}{e.audible_asin ? e.total : '?'} {t('Bänden')}</dd></div>
              ))}
              <div><dt className="text-faint">{t('Laufzeit im Bestand')}</dt><dd className="tabular font-semibold">{s.hours} {t('Stunden')}{s.hours_editions && s.hours_editions > 1 ? <span className="font-normal text-muted"> {t('(Mittel aus')}{' '}{s.hours_editions} {t('Fassungen)')}</span> : null}</dd></div>
              <div><dt className="text-faint">{t('Gehört')}</dt><dd className="tabular font-semibold">{s.listened} {t('von')}{' '}{s.owned}</dd></div>
              {s.next_release && <div><dt className="text-faint">{t('Nächster Band')}</dt><dd className="tabular font-semibold">{fmtDate(s.next_release)}</dd></div>}
            </dl>
            <div className="mt-auto flex flex-wrap items-center gap-2 pt-2">
              <Button variant="primary" disabled={open.length === 0 || auto.data?.running} onClick={startAuto}
                title={open.length ? t('Sucht alle fehlenden Bände und lädt sichere Treffer sofort') : t('Kein fehlender Band in den überwachten Fassungen')}>
                <WandSparkles size={16} aria-hidden />{t('Automatische Suche')}
              </Button>
              {admin && (
                <Button disabled={open.length === 0} onClick={() => setSearchBook(open[0].id)}
                  title={open.length ? t('Treffer aller Quellen ansehen und selbst auswählen') : t('Kein fehlender Band in den überwachten Fassungen')}>
                  <ListFilter size={16} aria-hidden />{t('Geführte Suche')}
                </Button>
              )}
              {admin && <Button onClick={refresh} disabled={refreshing}><RefreshCw size={15} aria-hidden className={refreshing ? 'animate-spin' : ''} />{refreshing ? t('Prüft Audible…') : t('Bei Audible neu prüfen')}</Button>}
              {admin && <Button onClick={findEbooks} title={t('Sucht für alle vorhandenen Bände ohne EPUB das E-Book: Usenet, sonst EBookBay und Internet Archive')}><BookOpen size={15} aria-hidden />{t('EPUBs suchen')}</Button>}
              <FollowButton kind="series" id={s.id} />
              <span className="text-xs text-muted">{open.length} {open.length === 1 ? t('Band fehlt') : t('Bände fehlen')}{t('. Durchsucht werden alle eingerichteten Quellen.')}</span>
              {ebookMsg && <p role="status" className="basis-full text-sm text-muted">{ebookMsg}</p>}
            </div>
          </div>
        </div>
      </div>
      {admin && <div className="mt-4 rounded-xl border border-line bg-surface p-4">
        <Switch on={s.auto} label={t('Automatik für diese Serie')}
          onChange={async v => { await api(`/series/${s.id}/auto`, { method: 'POST', body: JSON.stringify({ auto: v }) }); reload() }}
          hint={t('Ist die automatische Suche unter Aufgaben eingeschaltet, sucht und lädt ABW fehlende Bände dieser Serie selbständig. Hier lässt sich das für diese eine Serie abschalten.')} />
      </div>}
      {autoOpen && auto.data && <AutoPanel state={auto.data} onClose={() => setAutoOpen(false)} onPick={setSearchBook} canPick={admin} />}
      {admin && searchBook !== null && open.length > 0 && (
        <SearchDialog books={open} initial={open.some(b => b.id === searchBook) ? searchBook : open[0].id}
          onClose={() => setSearchBook(null)} onStarted={reload} />
      )}
      <ToReadKeys.Provider value={{ keys: new Set(toread.data ?? []), reload: toread.reload }}>
        {s.editions.map(e => <EditionPanel key={e.id} e={e} sid={s.id} absUrl={s.abs_url} canUnlink={s.editions.length > 1} canMark={s.can_mark} onChange={reload} onSearch={setSearchBook} />)}
      </ToReadKeys.Provider>
      {admin && (Object.entries(s.candidates) as [Lang, Candidate[]][]).map(([lang, items]) => <Candidates key={lang} lang={lang} items={items} sid={s.id} onChange={reload} />)}
    </main>
  )
}
