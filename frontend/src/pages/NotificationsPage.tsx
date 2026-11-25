import { useState } from 'react'
import { Link } from 'react-router-dom'
import { BellOff, BellRing, CheckCheck, Mic, Search, Trash2, Volume2, VolumeX } from 'lucide-react'
import { api, useData } from '../api'
import { Button, Chip, Cover, Empty, ErrorBox, Page } from '../ui'
import { NoteRow, type Notes } from '../Notifications'
import { Switch } from '../Automation'
import { t } from '../i18n'

interface Follows { series: number[]; authors: string[]; narrators?: string[] }
interface Overview {
  series: { id: number; name: string; author: string; author_key: string; cover: string | null; muted?: boolean }[]
  authors: { key: string; name: string; series: number }[]
  narrators: { key: string; name: string; books: number }[]
}

/** Übersicht, zu welchen Serien und Autoren der Benutzer Benachrichtigungen bekommt, mit Abstellen pro Eintrag.
 *  Abgestellte Einträge bleiben bis zum Verlassen sichtbar und lassen sich wieder einschalten. */
function Marked({ onChange }: { onChange: () => void }) {
  const { data, error, reload } = useData<Overview>('/follows/overview')
  const [off, setOff] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [mutes, setMutes] = useState<Record<number, boolean>>({})
  const isMuted = (s: { id: number; muted?: boolean }) => mutes[s.id] ?? !!s.muted
  const mute = async (s: { id: number; muted?: boolean }) => {
    const on = !isMuted(s)
    setBusy(`mute:${s.id}`)
    try {
      await api('/follow/mute', { method: 'POST', body: JSON.stringify({ series_id: s.id, on }) })
      setMutes(m => ({ ...m, [s.id]: on }))
    } finally { setBusy(null) }
  }
  const [narr, setNarr] = useState('')
  const [narrErr, setNarrErr] = useState<string | null>(null)
  const addNarrator = async () => {
    setNarrErr(null)
    try { await api('/follow', { method: 'POST', body: JSON.stringify({ kind: 'narrator', key: narr, on: true }) }); setNarr(''); reload(); onChange() }
    catch (e) { setNarrErr((e as Error).message) }
  }
  const toggle = async (kind: 'series' | 'author' | 'narrator', key: string) => {
    const id = `${kind}:${key}`
    setBusy(id)
    try {
      await api('/follow', { method: 'POST', body: JSON.stringify({ kind, key, on: off.has(id) }) })
      setOff(prev => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n })
      onChange()
    } finally { setBusy(null) }
  }
  if (error && !data) return <ErrorBox message={error} onRetry={reload} />
  if (!data) return <div className="skeleton h-64 rounded-2xl" />
  const narrators = (
    <section>
      <h2 className="mb-2 text-sm font-semibold text-muted">{t('Sprecher ({{v0}})', { v0: data.narrators.length })}</h2>
      <form className="mb-2 flex max-w-md gap-2" onSubmit={e => { e.preventDefault(); if (narr.trim()) addNarrator() }}>
        <input value={narr} onChange={e => setNarr(e.target.value)} placeholder={t('Name des Sprechers, wie bei Audible')} aria-label={t('Sprecher markieren')}
          className="min-h-10 w-full rounded-lg border border-line bg-surface px-3 text-sm" />
        <Button type="submit" disabled={!narr.trim()}><Mic size={15} aria-hidden />{t('Markieren')}</Button>
      </form>
      {narrErr && <p role="status" className="mb-2 text-sm">{narrErr}</p>}
      {data.narrators.length > 0 && (
        <ul className="divide-y divide-line rounded-xl border border-line bg-surface p-1.5">
          {data.narrators.map(a => (
            <li key={a.key} className={`flex items-center gap-3 p-2 ${off.has(`narrator:${a.key}`) ? 'opacity-55' : ''}`}>
              <div className="min-w-0 flex-1">
                <span className="block truncate font-medium">{a.name}</span>
                <span className="text-sm text-muted">{t('{{v0}} Bücher im Bestand', { v0: a.books })}</span>
              </div>
              {btn('narrator', a.key, a.name)}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
  if (!data.series.length && !data.authors.length && !data.narrators.length) return (
    <div className="space-y-6">
    <Empty title={t('Nichts markiert')} text={t('Öffne eine Serie oder einen Autor und drücke «Markieren»; was du unter Entdecken hinzufügst, ist automatisch markiert.')}
      action={<Link to="/" className="text-sm font-semibold text-accent underline-offset-4 hover:underline">{t('Zu den Serien')}</Link>} />
    {narrators}
    </div>
  )
  const needle = q.trim().toLowerCase()
  const series = data.series.filter(s => !needle || `${s.name} ${s.author}`.toLowerCase().includes(needle))
  const authors = data.authors.filter(a => !needle || a.name.toLowerCase().includes(needle))
  const btn = (kind: 'series' | 'author' | 'narrator', key: string, label: string) => {
    const id = `${kind}:${key}`
    const isOff = off.has(id)
    return (
      <Button onClick={() => toggle(kind, key)} disabled={busy === id}
        title={isOff ? t('Benachrichtigungen zu {{v0}} wieder einschalten', { v0: label }) : t('Keine Benachrichtigungen mehr zu {{v0}}', { v0: label })}>
        {isOff ? <><BellRing size={15} aria-hidden />{t('Wieder einschalten')}</> : <><BellOff size={15} aria-hidden />{t('Abstellen')}</>}
      </Button>
    )
  }
  const row = 'flex items-center gap-3 p-2'
  return (
    <div className="space-y-6">
      <label className="flex max-w-sm items-center gap-2 rounded-lg border border-line bg-surface px-3">
        <Search size={15} aria-hidden className="text-faint" />
        <input value={q} onChange={e => setQ(e.target.value)} placeholder={t('Serie oder Autor suchen')} aria-label={t('Markierte Serien und Autoren durchsuchen')}
          className="min-h-10 w-full bg-transparent text-sm outline-none" />
      </label>
      <section>
        <h2 className="mb-2 text-sm font-semibold text-muted">{t('Serien (')}{data.series.length})</h2>
        {series.length === 0 ? <p className="text-sm text-faint">{needle ? t('Keine markierte Serie passt.') : t('Keine Serie markiert.')}</p> : (
          <ul className="divide-y divide-line rounded-xl border border-line bg-surface p-1.5">
            {series.map(s => (
              <li key={s.id} className={`${row} ${off.has(`series:${s.id}`) ? 'opacity-55' : ''}`}>
                <Link to={`/serie/${s.id}`} className="w-12 shrink-0 overflow-hidden rounded-md"><Cover src={s.cover} alt={s.name} /></Link>
                <div className="min-w-0 flex-1">
                  <Link to={`/serie/${s.id}`} className="block truncate font-medium hover:underline">{s.name}</Link>
                  <Link to={`/autor/${s.author_key}`} className="block truncate text-sm text-muted hover:underline">{s.author}</Link>
                  {isMuted(s) && <span className="block text-xs text-faint">{t('Stumm: bleibt markiert und im Kalender, meldet aber nichts')}</span>}
                </div>
                {!off.has(`series:${s.id}`) && (
                  <Button onClick={() => mute(s)} disabled={busy === `mute:${s.id}`}
                    title={isMuted(s) ? t('Meldungen zu dieser Serie wieder einschalten') : t('Serie stummschalten: bleibt markiert, meldet aber nichts')}>
                    {isMuted(s) ? <><Volume2 size={15} aria-hidden />{t('Laut')}</> : <><VolumeX size={15} aria-hidden />{t('Stumm')}</>}
                  </Button>
                )}
                {btn('series', String(s.id), t('dieser Serie'))}
              </li>
            ))}
          </ul>
        )}
      </section>
      <section>
        <h2 className="mb-2 text-sm font-semibold text-muted">{t('Autoren (')}{data.authors.length})</h2>
        {authors.length === 0 ? <p className="text-sm text-faint">{needle ? t('Kein markierter Autor passt.') : t('Kein Autor markiert.')}</p> : (
          <ul className="divide-y divide-line rounded-xl border border-line bg-surface p-1.5">
            {authors.map(a => (
              <li key={a.key} className={`${row} ${off.has(`author:${a.key}`) ? 'opacity-55' : ''}`}>
                <div className="min-w-0 flex-1">
                  <Link to={`/autor/${a.key}`} className="block truncate font-medium hover:underline">{a.name}</Link>
                  <span className="text-sm text-muted">{a.series === 1 ? t('1 Serie in der Bibliothek') : t('{{v0}} Serien in der Bibliothek', { v0: a.series })}</span>
                </div>
                {btn('author', a.key, t('diesem Autor'))}
              </li>
            ))}
          </ul>
        )}
      </section>
      {narrators}
    </div>
  )
}

interface Prefs { kinds: { kind: string; label: string; on: boolean }[]; digest: boolean; muted: number[] }

const KIND_HINT: Record<string, string> = {
  announced: t('Audible nennt einen neuen Band einer markierten Serie.'),
  new_book: t('Ein neuer Band ist bei Audible schon erschienen, als ABW ihn zum ersten Mal sah.'),
  released: t('Der Erscheinungstag eines angekündigten Bandes ist da; ABW sucht ihn jetzt.'),
  available: t('Ein Buch ist in der Bibliothek angekommen.'),
  new_series: t('Ein markierter Autor hat eine neue Serie.'),
  narrator: t('Ein markierter Sprecher liest ein neues Hörbuch (Audible.com und Audible.de, täglich geprüft).'),
  epub: t('ABW hat ein E-Book neben ein Hörbuch gelegt.'),
  recommend: t('Ein anderer Benutzer empfiehlt dir eine Serie.'),
  rate: t('Nach dem Hören: Erinnerung, das Buch zu bewerten.'),
}

/** Welche Meldungen der Benutzer bekommt, einzeln abschaltbar, und Push einzeln oder als Wochen-Zusammenfassung. */
function Settings() {
  const { data, error, reload } = useData<Prefs>('/notifications/prefs')
  const [state, setState] = useState<Prefs | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const p = state ?? data
  const save = async (body: { kinds?: Record<string, boolean>; digest?: boolean }) => {
    setErr(null)
    try { setState(await api<Prefs>('/notifications/prefs', { method: 'POST', body: JSON.stringify(body) })) } catch (e) { setErr((e as Error).message) }
  }
  if (error && !p) return <ErrorBox message={error} onRetry={reload} />
  if (!p) return <div className="skeleton h-64 rounded-2xl" />
  return (
    <div className="max-w-2xl space-y-8">
      {err && <ErrorBox message={err} plain />}
      <section>
        <h2 className="mb-1 text-sm font-semibold text-muted">{t('Was du bekommst')}</h2>
        <p className="mb-3 text-sm text-faint">{t('Gilt für die Glocke und für Push. Ausgeschaltete Arten kommen gar nicht mehr.')}</p>
        <div className="space-y-3 rounded-xl border border-line bg-surface p-4">
          {p.kinds.map(k => <Switch key={k.kind} on={k.on} onChange={v => save({ kinds: { [k.kind]: v } })} label={k.label} hint={KIND_HINT[k.kind] ?? ''} />)}
        </div>
      </section>
      <section>
        <h2 className="mb-3 text-sm font-semibold text-muted">{t('Push aufs Handy')}</h2>
        <div className="rounded-xl border border-line bg-surface p-4">
          <Switch on={p.digest} onChange={v => save({ digest: v })} label={t('Wochen-Zusammenfassung statt einzeln')}
            hint={t('Push kommt einmal pro Woche als eine Meldung mit allem Neuen. In der Glocke steht weiter alles sofort.')} />
        </div>
      </section>
      <p className="text-sm text-faint">{t('Einzelne Serien schaltest du unter «Markiert» stumm: Sie bleiben markiert und im Kalender, melden aber nichts.')}</p>
    </div>
  )
}

export default function NotificationsPage() {
  const [view, setView] = useState<'all' | 'unread' | 'marked' | 'settings'>('all')
  const unreadOnly = view === 'unread'
  const [limit, setLimit] = useState(50)
  const { data, error, reload } = useData<Notes>(`/notifications?limit=${limit}&unread=${unreadOnly ? 1 : 0}`, 30000)
  const { data: follows, reload: reloadFollows } = useData<Follows>('/follows')
  const marked = follows ? follows.series.length + follows.authors.length : null
  const read = async (ids?: number[]) => { await api('/notifications/read', { method: 'POST', body: JSON.stringify(ids ? { ids } : {}) }); reload() }
  const clear = async () => { await api('/notifications/clear', { method: 'POST' }); reload() }
  return (
    <Page title={t('Benachrichtigungen')}
      subtitle={marked === null ? t('Neues zu deinen markierten Serien und Autoren')
        : t('Neues zu deinen markierten Serien und Autoren ({{v0}} {{v1}}, {{v2}} {{v3}})', { v0: follows!.series.length, v1: follows!.series.length === 1 ? t('Serie') : t('Serien'), v2: follows!.authors.length, v3: follows!.authors.length === 1 ? t('Autor') : t('Autoren') })}
      actions={(view === 'all' || view === 'unread') && data && data.total > 0 ? (
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => read()} disabled={!data.unread}><CheckCheck size={15} aria-hidden />{t('Alle als gelesen markieren')}</Button>
          <Button onClick={clear} title={t('Entfernt alle gelesenen Benachrichtigungen aus der Liste')}><Trash2 size={15} aria-hidden />{t('Gelesene entfernen')}</Button>
        </div>
      ) : undefined}>
      <div className="mb-4 flex flex-wrap gap-2">
        <Chip active={view === 'all'} onClick={() => setView('all')}>{t('Alle')}</Chip>
        <Chip active={view === 'unread'} onClick={() => setView('unread')} count={data?.unread}>{t('Ungelesen')}</Chip>
        <Chip active={view === 'marked'} onClick={() => setView('marked')} count={marked ?? undefined}>{t('Markiert')}</Chip>
        <Chip active={view === 'settings'} onClick={() => setView('settings')}>{t('Einstellungen')}</Chip>
      </div>
      {view === 'settings' ? <Settings />
        : view === 'marked' ? <Marked onChange={reloadFollows} />
        : error && !data ? <ErrorBox message={error} onRetry={reload} />
        : !data ? <div className="skeleton h-64 rounded-2xl" />
        : data.items.length === 0 ? (
          <Empty title={unreadOnly ? t('Alles gelesen') : t('Noch keine Benachrichtigungen')}
            text={marked === 0
              ? t('Du hast noch nichts markiert. Öffne eine Serie oder einen Autor und drücke «Markieren»; was du unter Entdecken hinzufügst, ist automatisch markiert.')
              : t('Hier erscheint, wenn ein neuer Band angekündigt wird, ein Hörbuch erscheint, ein Buch im Bestand ankommt oder ein Autor eine neue Serie hat.')}
            action={marked === 0 ? <Link to="/" className="text-sm font-semibold text-accent underline-offset-4 hover:underline">{t('Zu den Serien')}</Link> : undefined} />
        ) : (
          <>
            <ul className="divide-y divide-line rounded-xl border border-line bg-surface p-1.5">
              {data.items.map(n => <li key={n.id}><NoteRow n={n} onOpen={() => { if (!n.read_at) read([n.id]) }} /></li>)}
            </ul>
            {data.total > data.items.length && <div className="mt-4 flex justify-center"><Button onClick={() => setLimit(limit + 100)}>{t('Ältere anzeigen')}</Button></div>}
          </>
        )}
    </Page>
  )
}
