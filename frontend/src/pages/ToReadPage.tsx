import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowDown, ArrowDownToLine, ArrowUp, ArrowUpToLine, Check, GripVertical, Headphones, Play, Send, Sparkles, ThumbsUp, X } from 'lucide-react'
import { api, fmtDate, fmtDateTime, fmtSeriesSize, useData, type BookState } from '../api'
import { Badge, Cover, Empty, ErrorBox, LangTag, Page, SkeletonGrid } from '../ui'
import { useAdmin } from '../session'
import { locale, t } from '../i18n'

interface TBook {
  id: number; title: string; sequence: string | null; author: string | null; narrator: string | null; runtime_min: number | null
  cover: string | null; abs_item_id: string | null; state: BookState; release_date: string | null; progress: number | null; finished: boolean
}
interface Stats { done: number; total: number; pct: number | null; hours_done: number; hours_total: number; min_left: number }
interface Entry {
  key: string; kind: 'edition' | 'book'; source: 'manual' | 'auto' | 'rec' | 'suggest'; series_id: number | null; series: string | null; author: string | null
  cover: string | null; lang: string; variant: string | null; left: number; stats: Stats; last: string; pinned?: boolean; eta?: string | null; book: TBook
  from?: string[]; why?: string[]
}
interface Overview { next: Entry[]; waiting: Entry[]; hours: number; pace_h: number | null; abs_url: string; recommended: Entry[]; suggested: Entry[] }
interface Heard {
  id: number; title: string; sequence: string | null; author: string | null; lang: string; series: string | null; series_id: number | null
  finished_at: string; key: string; cover: string | null; sent_to: string[]; count: number
  series_books: number | null; series_hours: number | null
  blocked: { id: number; name: string; why: 'heard' | 'listed' }[]
}
const WHY = { heard: t('hat sie schon gehört'), listed: t('hat sie auf To Read') } as const
interface Pick {
  asin: string; lang: string; title: string; author: string; series: string | null; series_asin: string | null; series_id: number | null
  series_books: number | null; series_min: number | null; cover_url: string | null; genre: string | null; explore: boolean; because: string[]; rating: number | null; in_library: boolean
}

const parse = (key: string) => ({ kind: key[0] === 'e' ? 'edition' : 'book', id: Number(key.slice(1)) })

/** Eine Zeile je Serie: Serienname, nächster Band, Fortschritt über die ganze Serie. */
function Row({ x, absUrl, onRemove, children, drag, actions }: {
  x: Entry; absUrl: string; onRemove?: () => void; children?: React.ReactNode; actions?: React.ReactNode
  drag?: { onStart: () => void; onOver: () => void; onDrop: () => void; active: boolean }
}) {
  const b = x.book
  const st = x.stats
  const play = b.abs_item_id && absUrl ? `${absUrl}/item/${b.abs_item_id}` : null
  const name = x.series ?? b.title
  const bookPct = Math.round((b.progress ?? 0) * 100)
  return (
    <li draggable={!!drag} onDragStart={drag?.onStart} onDragOver={e => { if (drag) { e.preventDefault(); drag.onOver() } }} onDrop={e => { e.preventDefault(); drag?.onDrop() }}
      className={`flex items-center gap-3 border-t border-line px-3 py-3 first:border-t-0 sm:px-4 ${drag?.active ? 'bg-surface-2' : ''} ${x.pinned ? 'shadow-[inset_3px_0_0_var(--accent)]' : ''}`}>
      {drag && <GripVertical size={16} aria-hidden className="hidden shrink-0 cursor-grab text-faint lg:block" />}
      <Cover src={x.cover ?? b.cover} alt={name} className={`w-14 shrink-0 rounded-md ${b.state === 'owned' ? '' : 'opacity-70 saturate-50'}`} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-semibold">
          {x.series_id ? <Link to={`/serie/${x.series_id}`} className="hover:text-accent">{name}</Link> : name}
        </p>
        <p className="truncate text-xs text-muted">
          {[x.author, x.series && (b.sequence ? t('weiter mit Band {{v0}}: {{v1}}', { v0: b.sequence, v1: b.title }) : b.title), bookPct > 0 && !b.finished && t('{{v0}} % gehört', { v0: bookPct })].filter(Boolean).join(' · ')}
        </p>
        {x.series && st.total > 0 && (
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
            <div className="h-1.5 w-full max-w-56 overflow-hidden rounded-full bg-surface-2" role="progressbar" aria-label={t('Fortschritt in {{v0}}', { v0: name })}
              aria-valuemin={0} aria-valuemax={100} aria-valuenow={st.pct ?? 0}>
              <div className="h-full rounded-full" style={{ width: `${st.pct ?? 0}%`, background: 'var(--accent)' }} />
            </div>
            <span className="tabular font-semibold text-text">{st.pct ?? 0} %</span>
            <span className="tabular">{st.done} {t('von')}{' '}{st.total} {t('Bänden gehört')}</span>
            {st.hours_total > 0 && <span className="tabular text-faint">{st.hours_done} {t('von')}{' '}{st.hours_total} h</span>}
            {x.eta && <span className="tabular font-medium text-text" title={t('Geschätzt aus deinem Hörtempo der letzten 30 Tage, in der Reihenfolge der Liste')}>{t('fertig ca.')}{' '}{fmtDate(x.eta)}</span>}
          </div>
        )}
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          <LangTag lang={x.lang} />
          {x.variant && <span className="rounded-full bg-surface-2 px-2 py-0.5 text-xs text-muted">{x.variant}</span>}
          {x.pinned && <span className="inline-flex items-center gap-1 rounded-full bg-surface-2 px-2 py-0.5 text-xs text-muted"><Headphones size={11} aria-hidden />{t('zuletzt gehört')}</span>}
          {x.source === 'auto' && <span className="inline-flex items-center gap-1 rounded-full bg-surface-2 px-2 py-0.5 text-xs text-muted" title={t('Automatisch eingereiht, weil du den Band davor fertig gehört hast')}><Sparkles size={11} aria-hidden />{t('automatisch')}</span>}
          {b.state !== 'owned' && <Badge status={b.state} label={b.state === 'upcoming' ? (b.release_date ? t('Erscheint {{v0}}', { v0: fmtDate(b.release_date) }) : t('Termin offen')) : undefined} />}
        </div>
        {x.from && x.from.length > 0 && (
          <p className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium" style={{ color: 'var(--accent)' }}>
            <ThumbsUp size={12} aria-hidden />{x.series ? t('Serie') : t('Buch')} {t('empfohlen von')}{' '}{x.from.join(', ')}
          </p>
        )}
        {x.why && x.why.length > 0 && <p className="mt-1.5 text-xs text-muted">{x.why.join(' · ')}{b.release_date ? t(' · Band erschienen {{v0}}', { v0: fmtDate(b.release_date) }) : ''}</p>}
        {actions && <div className="mt-2 flex flex-wrap gap-1.5">{actions}</div>}
      </div>
      <div className="flex shrink-0 flex-col items-center">
        {children}
        {play && (
          <a href={play} target="_blank" rel="noreferrer" aria-label={t('{{v0}} in Audiobookshelf abspielen', { v0: b.title })} title={t('Nächsten Band in Audiobookshelf öffnen')}
            className="flex h-8 w-9 items-center justify-center rounded-lg text-accent hover:bg-surface-2"><Play size={16} aria-hidden /></a>
        )}
        {onRemove && (
          <button type="button" onClick={onRemove} aria-label={t('{{v0}} von To Read entfernen', { v0: name })} title={t('Von To Read entfernen')}
            className="flex h-8 w-9 items-center justify-center rounded-lg text-faint hover:bg-surface-2 hover:text-text"><X size={16} aria-hidden /></button>
        )}
      </div>
    </li>
  )
}

function Block({ title, hint, children }: { title: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="mb-8">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold">{title}</h2>
        {hint && <span className="text-sm text-muted">{hint}</span>}
      </div>
      <ul className="overflow-hidden rounded-xl border border-line bg-surface">{children}</ul>
    </section>
  )
}

/** Drei Serien aus «Für dich»: drei Genres, eine davon «Mal was anderes». Lädt separat nach,
 *  weil «Für dich» beim ersten Aufruf länger braucht. */
function Picks({ onChange }: { onChange: () => void }) {
  const { data, error, loading, reload } = useData<Pick[]>('/toread/picks')
  const [err, setErr] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const decide = async (p: Pick, action: Action) => {
    setErr(null); setNote(null)
    try {
      const r = await api<{ pending?: boolean; message?: string; library?: boolean }>('/toread/picks/decide', { method: 'POST', body: JSON.stringify({ ...p, action }) })
      if (r.pending) setNote(r.message ?? t('Vorgemerkt.'))
      else if (r.library) setNote(t('«{{v0}}» in die Bibliothek aufgenommen und auf die Liste gesetzt.', { v0: p.series ?? p.title }))
      reload(); onChange()
    } catch (e) { setErr((e as Error).message) }
  }
  return (
    <section className="mb-8">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold">{t('Aus Entdecken')}</h2>
        <span className="text-sm text-muted">{t('drei Genres, eines davon mal was anderes')}</span>
      </div>
      {err && <ErrorBox message={err} plain />}
      {note && <p role="status" className="mb-2 text-sm text-muted">{note}</p>}
      {error && !data ? <ErrorBox message={error} onRetry={reload} />
        : loading && !data ? <p className="rounded-xl border border-line bg-surface px-4 py-6 text-sm text-muted">{t('Fragt Audible nach passenden Serien…')}</p>
        : !data?.length ? <p className="rounded-xl border border-dashed border-line px-4 py-6 text-center text-sm text-muted">{t('Gerade keine neuen Vorschläge.')}</p>
        : (
          <ul className="overflow-hidden rounded-xl border border-line bg-surface">
            {data.map(p => (
              <li key={p.asin} className="flex items-center gap-3 border-t border-line px-3 py-3 first:border-t-0 sm:px-4">
                <Cover src={p.cover_url} alt={p.series ?? p.title} className="w-14 shrink-0 rounded-md" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold">
                    {p.series_id ? <Link to={`/serie/${p.series_id}`} className="hover:text-accent">{p.series ?? p.title}</Link>
                      : <a href={`https://www.audible.${p.lang === 'de' ? 'de' : 'com'}/pd/${p.asin}`} target="_blank" rel="noreferrer" className="hover:text-accent">{p.series ?? p.title}</a>}
                  </p>
                  <p className="truncate text-xs text-muted">{[p.author, p.series && fmtSeriesSize(p.series_books, p.series_min), p.rating && t('★ {{v0}}', { v0: p.rating.toLocaleString(locale()) })].filter(Boolean).join(' · ')}</p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    <LangTag lang={p.lang} />
                    {p.genre && <span className="rounded-full border border-line px-2 py-0.5 text-[11px] text-muted">{p.genre}</span>}
                    {p.explore && <span className="inline-flex items-center gap-1 rounded-full bg-surface-2 px-2 py-0.5 text-xs" style={{ color: 'var(--info)' }}><Sparkles size={11} aria-hidden />{t('Mal was anderes')}</span>}
                  </div>
                  {p.because.length > 0 && <p className="mt-1 line-clamp-1 text-xs text-faint">{t('Ähnlich wie')}{' '}{p.because.join(', ')}</p>}
                  {!p.in_library && <p className="mt-1 text-xs text-faint">{t('Noch nicht in ABW: kommt beim Übernehmen in die Bibliothek und wird gesucht.')}</p>}
                  <div className="mt-2 flex flex-wrap gap-1.5"><Decide name={p.series ?? p.title} onPick={a => decide(p, a)} /></div>
                </div>
              </li>
            ))}
          </ul>
        )}
    </section>
  )
}

function ListTab() {
  const { data, error, loading, reload } = useData<Overview>('/toread')
  const [order, setOrder] = useState<string[] | null>(null)  // Reihenfolge während und direkt nach dem Verschieben
  const [dragKey, setDragKey] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  if (error && !data) return <ErrorBox message={error} onRetry={reload} />
  if (loading && !data) return <SkeletonGrid n={6} />
  const d = data!
  // Die zuletzt gehörte Serie steht fest zuoberst; verschoben wird nur der Rest
  const pinned = d.next.filter(x => x.pinned)
  const rest = d.next.filter(x => !x.pinned)
  const keysNow = rest.map(x => x.key)
  const keys = order && order.length === keysNow.length && order.every(k => keysNow.includes(k)) ? order : keysNow
  const sorted = keys.map(k => rest.find(x => x.key === k)!)
  const save = async (k: string[]) => {
    setOrder(k); setErr(null)
    try { await api('/toread/order', { method: 'PUT', body: JSON.stringify({ keys: [...pinned.map(x => x.key), ...k] }) }); reload() } catch (e) { setErr((e as Error).message); setOrder(null) }
  }
  const move = (i: number, by: number) => {
    const k = [...keys]; const j = i + by
    if (j < 0 || j >= k.length) return
    ;[k[i], k[j]] = [k[j], k[i]]
    save(k)
  }
  const remove = async (key: string) => {
    const { kind, id } = parse(key)
    setErr(null)
    try { await api(`/toread/${kind}/${id}`, { method: 'DELETE' }); reload() } catch (e) { setErr((e as Error).message) }
  }
  const decide = async (path: string, action: Action) => {
    setErr(null)
    try { await api(path, { method: 'POST', body: JSON.stringify({ action }) }); reload() } catch (e) { setErr((e as Error).message) }
  }
  const dragOver = (target: string) => {
    if (!dragKey || dragKey === target) return
    const k = keys.filter(x => x !== dragKey)
    k.splice(keys.indexOf(target), 0, dragKey)
    setOrder(k)
  }
  return (
    <>
      {err && <ErrorBox message={err} plain />}
      {!d.next.length && !d.waiting.length && !d.recommended.length && !d.suggested.length && <Empty title={t('Noch nichts geplant')} text={t('Setze in einer Serie «Zu To Read». Hörst du einen Band fertig und folgt ein weiterer, kommt die Serie von selbst auf die Liste.')} />}
      {d.next.length > 0 && (
        <Block title={t('Als Nächstes')} hint={t('{{v0}} auf der Liste · nächste Bände rund {{v1}} h{{v2}}', { v0: d.next.length, v1: d.hours, v2: d.pace_h ? ` · dein Tempo: ${d.pace_h.toLocaleString(locale())} h pro Tag (letzte 30 Tage)` : '' })}>
          {pinned.map(x => <Row key={x.key} x={x} absUrl={d.abs_url} onRemove={() => remove(x.key)} />)}
          {sorted.map((x, i) => (
            <Row key={x.key} x={x} absUrl={d.abs_url} onRemove={() => remove(x.key)}
              drag={{ onStart: () => setDragKey(x.key), onOver: () => dragOver(x.key), onDrop: () => { setDragKey(null); save(keys) }, active: dragKey === x.key }}>
              <div className="contents">
              <button type="button" onClick={() => move(i, -1)} disabled={i === 0} aria-label={`${x.series ?? x.book.title} nach oben`}
                className="flex h-8 w-9 items-center justify-center rounded-lg text-muted hover:bg-surface-2 hover:text-text disabled:opacity-30"><ArrowUp size={16} aria-hidden /></button>
              <button type="button" onClick={() => move(i, 1)} disabled={i === sorted.length - 1} aria-label={`${x.series ?? x.book.title} nach unten`}
                className="flex h-8 w-9 items-center justify-center rounded-lg text-muted hover:bg-surface-2 hover:text-text disabled:opacity-30"><ArrowDown size={16} aria-hidden /></button>
              </div>
            </Row>
          ))}
        </Block>
      )}
      {d.waiting.length > 0 && (
        <Block title={t('Wartet')} hint={t('nächster Band noch nicht im Bestand')}>
          {d.waiting.map(x => <Row key={x.key} x={x} absUrl={d.abs_url} onRemove={() => remove(x.key)} />)}
        </Block>
      )}
      {d.recommended.length > 0 && (
        <Block title={t('Empfohlen')} hint={t('von anderen Benutzern')}>
          {d.recommended.map(x => <Row key={x.key} x={x} absUrl={d.abs_url} actions={<Decide name={x.series ?? x.book.title} onPick={a => decide(`/toread/rec/${x.key}`, a)} />} />)}
        </Block>
      )}
      {d.suggested.length > 0 && (
        <Block title={t('Vorschläge')} hint={t('hoch bewertet oder fast fertig gehört, mit neuem Band')}>
          {d.suggested.map(x => <Row key={x.key} x={x} absUrl={d.abs_url} actions={<Decide name={x.series ?? x.book.title} onPick={a => decide(`/toread/suggest/${x.key.slice(1)}`, a)} />} />)}
        </Block>
      )}
      <Picks onChange={reload} />
    </>
  )
}

type Action = 'top' | 'bottom' | 'no'

/** Drei Knöpfe für Empfehlungen und Vorschläge: zuoberst, zuunterst oder nicht auf die Liste. */
function Decide({ name, onPick }: { name: string; onPick: (a: Action) => Promise<void> }) {
  const [busy, setBusy] = useState<Action | null>(null)
  const btn = 'inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-line px-3 text-xs font-semibold transition-colors hover:border-accent hover:text-accent disabled:opacity-50'
  const pick = async (a: Action) => { setBusy(a); try { await onPick(a) } finally { setBusy(null) } }
  return (
    <>
      <button type="button" className={btn} disabled={!!busy} onClick={() => pick('top')} aria-label={t('{{v0}} zuoberst auf die Liste', { v0: name })}><ArrowUpToLine size={14} aria-hidden />{t('Zuoberst')}</button>
      <button type="button" className={btn} disabled={!!busy} onClick={() => pick('bottom')} aria-label={t('{{v0}} zuunterst auf die Liste', { v0: name })}><ArrowDownToLine size={14} aria-hidden />{t('Zuunterst')}</button>
      <button type="button" className={btn} disabled={!!busy} onClick={() => pick('no')} aria-label={t('{{v0}} nicht auf die Liste', { v0: name })}><X size={14} aria-hidden />{t('Nicht auf die Liste')}</button>
    </>
  )
}

/** Auswahl der Benutzer, denen eine Serie empfohlen wird. */
function RecommendDialog({ book, onClose, onSent }: { book: Heard; onClose: () => void; onSent: () => void }) {
  const users = useData<{ id: number; name: string }[]>('/toread/users')
  const [pick, setPick] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const what = book.series ?? book.title
  const send = async () => {
    setBusy(true); setErr(null)
    try {
      await api('/toread/recommend', { method: 'POST', body: JSON.stringify({ book_id: book.id, to: [...pick] }) })
      onSent(); onClose()
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  return (
    <div className="fixed inset-0 z-40 flex items-end justify-center bg-black/50 p-3 sm:items-center" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-labelledby="rec-title" onClick={e => e.stopPropagation()}
        className="w-full max-w-sm rounded-2xl border border-line bg-surface p-4 shadow-2xl">
        <h2 id="rec-title" className="text-base font-semibold">«{what}{t('» weiterempfehlen')}</h2>
        <p className="mt-1 text-sm text-muted">{book.series ? t('Die Serie erscheint') : t('Das Buch erscheint')} {t('bei den gewählten Benutzern unter ihrer Liste «To Read».')}</p>
        {users.error && <ErrorBox message={users.error} plain />}
        {users.data && users.data.length === 0 && <p className="mt-3 text-sm text-muted">{t('Es gibt keine anderen Benutzer in ABW.')}</p>}
        <ul className="mt-3 flex max-h-72 flex-col gap-1 overflow-y-auto">
          {(users.data ?? []).map(u => {
            const on = pick.has(u.id)
            const already = book.sent_to.includes(u.name)
            const block = book.blocked.find(b => b.id === u.id)
            return (
              <li key={u.id}>
                <label className={`flex min-h-11 items-center gap-3 rounded-lg px-2 ${block ? 'opacity-60' : 'cursor-pointer hover:bg-surface-2'}`}>
                  <input type="checkbox" checked={on} disabled={!!block} className="h-4 w-4 accent-[var(--accent)]"
                    onChange={() => setPick(p => { const n = new Set(p); if (on) n.delete(u.id); else n.add(u.id); return n })} />
                  <span className="text-sm">{u.name}</span>
                  {(block || already) && <span className="ml-auto text-xs text-faint">{block ? WHY[block.why] : t('schon empfohlen')}</span>}
                </label>
              </li>
            )
          })}
        </ul>
        {err && <ErrorBox message={err} plain />}
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="min-h-11 rounded-lg px-4 text-sm text-muted hover:text-text">{t('Abbrechen')}</button>
          <button type="button" onClick={send} disabled={busy || pick.size === 0}
            className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-accent-strong px-4 text-sm font-semibold text-on-accent disabled:opacity-50">
            <Send size={15} aria-hidden />{busy ? t('Sendet…') : t('Empfehlen')}
          </button>
        </div>
      </div>
    </div>
  )
}

/** Gehörte Bücher, zuletzt gehörtes zuerst, mit Knopf «Weiterempfehlen». */
function RecommendTab() {
  const { data, error, loading, reload } = useData<Heard[]>('/toread/heard')
  const admin = useAdmin()
  // Benutzer haben keine Auswahl: sie empfehlen direkt dem Administrator
  const fixed = useData<{ id: number; name: string }[]>(admin ? null : '/toread/users').data
  const [busy, setBusy] = useState<number | null>(null)
  const [open, setOpen] = useState<Heard | null>(null)
  const [shown, setShown] = useState(50)
  const [sent, setSent] = useState<string | null>(null)
  if (error && !data) return <ErrorBox message={error} onRetry={reload} />
  if (loading && !data) return <SkeletonGrid n={6} />
  const list = data ?? []
  const to = fixed ?? []
  const direct = async (b: Heard) => {
    setBusy(b.id); setSent(null)
    try {
      await api('/toread/recommend', { method: 'POST', body: JSON.stringify({ book_id: b.id, to: to.filter(u => !b.blocked.some(x => x.id === u.id)).map(u => u.id) }) })
      setSent(t('«{{v0}}» {{v1}} empfohlen.', { v0: b.series ?? b.title, v1: to.map(u => u.name).join(', ') })); reload()
    } catch (e) { setSent((e as Error).message) } finally { setBusy(null) }
  }
  if (!list.length) return <Empty title={t('Noch nichts gehört')} text={t('Sobald du ein Buch fertig gehört hast, kannst du seine Serie hier weiterempfehlen.')} />
  return (
    <>
      <p className="mb-3 text-sm text-muted">{t('Serien, aus denen du Bände fertig gehört hast, zuletzt gehörte zuerst. Empfohlen wird die ganze Serie')}{admin ? '' : t(', an {{v0}}', { v0: to.map(u => u.name).join(', ') || 'den Administrator' })}.</p>
      {sent && <p role="status" className="mb-3 inline-flex items-center gap-1.5 text-sm" style={{ color: 'var(--ok)' }}><Check size={15} aria-hidden />{sent}</p>}
      <ul className="overflow-hidden rounded-xl border border-line bg-surface">
        {list.slice(0, shown).map(b => (
          <li key={b.id} className="flex items-center gap-3 border-t border-line px-3 py-2.5 first:border-t-0 sm:px-4">
            <Cover src={b.cover} alt={b.title} className="w-12 shrink-0 rounded-md" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold">
                {b.series_id ? <Link to={`/serie/${b.series_id}`} className="hover:text-accent">{b.series ?? b.title}</Link> : b.series ?? b.title}
              </p>
              <p className="truncate text-xs text-muted">{[b.author, b.series && t('{{v0}} {{v1}} gehört', { v0: b.count, v1: b.count === 1 ? 'Band' : 'Bände' }), b.series && b.series_books && t('Serie: {{v0}}', { v0: fmtSeriesSize(b.series_books, b.series_hours ? b.series_hours * 60 : null) })].filter(Boolean).join(' · ')}</p>
              <p className="truncate text-xs text-faint">
                {t('Zuletzt')}{' '}{b.series && b.sequence ? t('Band {{v0}} ', { v0: b.sequence }) : ''}{t('gehört')}{' '}{fmtDateTime(b.finished_at)}{b.sent_to.length > 0 ? t(' · empfohlen an {{v0}}', { v0: b.sent_to.join(', ') }) : ''}
                {admin && b.blocked.length > 0 && t(' · nicht möglich bei {{v0}}', { v0: b.blocked.map(x => x.name).join(', ') })}
              </p>
            </div>
            {admin ? (
              <button type="button" onClick={() => setOpen(b)}
                className="inline-flex min-h-10 shrink-0 items-center gap-1.5 rounded-lg border border-line px-3 text-xs font-semibold hover:border-accent hover:text-accent">
                <ThumbsUp size={14} aria-hidden /><span className="hidden sm:inline">{t('Weiterempfehlen')}</span><span className="sm:hidden">{t('Empfehlen')}</span>
              </button>
            ) : to.length > 0 && to.every(u => b.blocked.some(x => x.id === u.id)) ? (
              <span className="max-w-32 shrink-0 text-right text-xs text-faint">
                {to.map(u => `${u.name} ${WHY[b.blocked.find(x => x.id === u.id)!.why]}`).join(', ')}
              </span>
            ) : (
              <button type="button" onClick={() => direct(b)} disabled={busy === b.id || !to.length || to.every(u => b.sent_to.includes(u.name))}
                className="inline-flex min-h-10 shrink-0 items-center gap-1.5 rounded-lg border border-line px-3 text-xs font-semibold hover:border-accent hover:text-accent disabled:opacity-50">
                {to.length && to.every(u => b.sent_to.includes(u.name)) ? <Check size={14} aria-hidden /> : <ThumbsUp size={14} aria-hidden />}
                {to.length && to.every(u => b.sent_to.includes(u.name)) ? t('Empfohlen') : busy === b.id ? t('Sendet…') : `${to.map(u => u.name).join(', ') || '…'} empfehlen`}
              </button>
            )}
          </li>
        ))}
      </ul>
      {shown < list.length && (
        <button type="button" onClick={() => setShown(shown + 50)} className="mt-3 min-h-11 w-full rounded-lg border border-line text-sm text-muted hover:text-text">
          {t('Weitere')}{' '}{Math.min(50, list.length - shown)} {t('von')}{' '}{list.length - shown} {t('anzeigen')}
        </button>
      )}
      {open && <RecommendDialog book={open} onClose={() => setOpen(null)} onSent={() => { setSent(t('«{{v0}}» empfohlen.', { v0: open.series ?? open.title })); reload() }} />}
    </>
  )
}

type Tab = 'list' | 'recommend'

/** Register kommen aus der Adresse (/to-read, /to-read/empfehlen), die Navigation zeigt sie an. */
export default function ToReadPage({ tab }: { tab: Tab }) {
  return tab === 'list'
    ? <Page title={t('To Read')} subtitle={t('Was du als Nächstes hörst, eine Zeile je Serie. Zuoberst steht die Serie, die du zuletzt gehört hast.')}><ListTab /></Page>
    : <Page title={t('Weiterempfehlen')} subtitle={t('Empfiehl Serien, die du gehört hast, anderen Benutzern.')}><RecommendTab /></Page>
}
