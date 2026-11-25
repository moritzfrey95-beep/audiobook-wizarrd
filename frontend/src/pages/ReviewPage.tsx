import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { AlertTriangle, CheckCircle2, ChevronDown, FolderInput, Info, Link2, Loader2, RotateCcw, Scissors, Search, Trash2, X } from 'lucide-react'
import { api, fmtDateTime, useData, type Book } from '../api'
import { SearchDialog } from '../SearchDialog'
import { ManualImport } from '../ManualImport'
import { ask, Badge, Button, Cover, Empty, ErrorBox, LangTag, Page, Tabs, UserTag } from '../ui'
import { locale, t } from '../i18n'

interface Check { name: string; status: 'ok' | 'warn' | 'fail'; text: string }
interface Plan { shown: string; files: string[]; bytes: number; problems: string[]; new_series_folder: boolean; overwritable?: boolean; existing?: { files: number; bytes: number } | null }
interface Dl {
  id: number; book_id: number; book_title: string; sequence: string | null; lang: string; series: string | null; series_id: number | null
  release_title: string; indexer: string | null; state: string; progress: number | null; error: string | null; auto: number; score: number | null
  created: string; updated: string; checks: Check[]; plan: Plan | null; import_enabled: boolean; imported_to: string | null
  finding?: string; children?: { id: number; state: string; error: string | null; title: string; sequence: string }[]
  cover: string | null; why?: string; kind?: 'ready' | 'hint' | 'blocked' | 'upgrade' | 'exists' | 'filing'; replaces?: number | null
  users?: string[]
}
interface Unsure { book_id: number; title: string; sequence: string | null; lang: string; series: string | null; series_id: number | null; unsure: number; last_search: string; cover: string | null; users?: string[] }
interface Bundle {
  book_id: number; title: string; rel_path: string; lang: string; state: 'split' | 'done' | 'partial' | 'unsafe' | 'skipped'; message: string
  at: string; cover: string | null; children: { id: number; state: string; title: string; sequence: string | null }[]
}
interface Review { decide: Dl[]; unsure: Unsure[]; active: Dl[]; history: Dl[]; bundles: Bundle[]; counts: Record<Exclude<Tab, 'pairs' | 'manual'>, number> }
interface Side { edition_id: number; series_id: number; name: string; lang: string; owned: number; count: number; hours: number; cover: string | null }
interface Pair { score: number; why: string[]; de: Side; en: Side; rank: number }

const TABS = { decide: t('Entscheiden'), unsure: t('Treffer wählen'), pairs: t('Sprachfassungen'), bundles: t('Sammelbände'), active: t('Unterwegs'), history: t('Verlauf'), manual: t('Handimport') } as const
type Tab = keyof typeof TABS

const STATE: Record<string, { status: 'downloading' | 'review' | 'failed' | 'owned'; label: string }> = {
  queued: { status: 'downloading', label: t('In der Warteschlange') }, downloading: { status: 'downloading', label: t('Lädt herunter') },
  arrived: { status: 'review', label: t('Angekommen, wird geprüft') }, failed: { status: 'failed', label: t('Download defekt') },
  verified: { status: 'owned', label: t('Prüfung bestanden') }, review: { status: 'review', label: t('Prüfung mit Hinweisen') },
  rejected: { status: 'failed', label: t('Verworfen') }, imported: { status: 'owned', label: t('Abgelegt, wartet auf Audiobookshelf') },
  done: { status: 'owned', label: t('Im Bestand') },
  split: { status: 'owned', label: t('Sammelband aufgeteilt') },
}
const FINDING: Record<string, string> = { long_unnamed: t('Viel länger als erwartet'), pack: t('Sammelpack'), pack_other: t('Sammelpack, andere Aufnahme'), short: t('Unvollständig oder gekürzt'), other: t('Andere Aufnahme') }
const CHILD_OK = new Set(['verified', 'imported', 'done'])
const CHILD_BAD = new Set(['rejected', 'failed'])
/** «Aufgeteilt in 7: 5 passen, 2 weichen ab» aus den Zuständen der entstandenen Einzelbände. */
function splitSummary(ch: NonNullable<Dl['children']>): string {
  const ok = ch.filter(k => CHILD_OK.has(k.state)).length, bad = ch.filter(k => CHILD_BAD.has(k.state)).length, open = ch.length - ok - bad
  return t('Aufgeteilt in {{v0}}: {{v1}} passen{{v2}}{{v3}}', { v0: ch.length, v1: ok, v2: bad ? t(', {{n}} weichen ab', { n: bad }) : '', v3: open ? t(', {{n}} noch in Prüfung', { n: open }) : '' })
}
const KIND = {
  ready: { label: t('Bereit zum Ablegen'), color: 'var(--ok)', icon: CheckCircle2 },
  filing: { label: t('Wird abgelegt'), color: 'var(--ok)', icon: Loader2 },
  hint: { label: t('Bitte ansehen'), color: 'var(--warn)', icon: Info },
  upgrade: { label: t('Besser als im Bestand'), color: 'var(--warn)', icon: Info },
  exists: { label: t('Zielordner existiert schon'), color: 'var(--warn)', icon: Info },
  blocked: { label: t('Ablage nicht möglich'), color: 'var(--bad)', icon: AlertTriangle },
}
const CHECK_COLOR = { ok: 'var(--ok)', warn: 'var(--warn)', fail: 'var(--bad)' }

function Head({ d }: { d: Dl }) {
  return (
    <div className="min-w-0 flex-1">
      <p className="flex flex-wrap items-center gap-2 text-sm">
        <LangTag lang={d.lang} />
        <UserTag users={d.users} />
        {d.series_id
          ? <Link to={`/serie/${d.series_id}`} className="min-w-0 truncate text-muted hover:text-accent">{d.series}{d.sequence ? t(', Band {{v0}}', { v0: d.sequence }) : ''}</Link>
          : <span className="text-muted">{t('Einzelbuch')}</span>}
      </p>
      <h3 className="mt-0.5 font-[family-name:var(--font-sans)] text-base font-semibold leading-snug tracking-normal">{d.book_title}</h3>
      <p className="mt-1 break-words text-xs text-muted">{d.release_title}</p>
      <p className="tabular mt-1 text-xs text-faint">{d.indexer} · {d.score ?? '–'} {t('Punkte ·')}{' '}{d.auto ? 'automatisch' : t('von Hand')} {t('gestartet')}{' '}{fmtDateTime(d.created)}</p>
    </div>
  )
}

function Checks({ checks }: { checks: Check[] }) {
  return (
    <ul className="space-y-1.5 text-sm">
      {checks.map(c => {
        const Icon = c.status === 'ok' ? CheckCircle2 : AlertTriangle
        return (
          <li key={c.name} className="flex gap-2">
            <Icon size={15} aria-hidden className="mt-0.5 shrink-0" style={{ color: CHECK_COLOR[c.status] }} />
            <span className="min-w-0 break-words"><span className="font-semibold">{c.name}:</span> <span className="text-muted">{c.text}</span></span>
          </li>
        )
      })}
    </ul>
  )
}

/** Ein Fall, der eine Entscheidung braucht: zuerst der Grund, dann die Knöpfe, Einzelheiten aufklappbar. */
function DecideCard({ d, onChange }: { d: Dl; onChange: () => void }) {
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [open, setOpen] = useState(d.kind !== 'ready')
  const k = KIND[d.kind ?? 'hint']
  const Icon = k.icon
  const act = async (action: 'import' | 'overwrite' | 'replace' | 'reject' | 'recheck', question?: string, yes?: string) => {
    if (question && !await ask(question, yes)) return
    setBusy(action); setErr(null)
    try { await api(`/downloads/${d.id}/${action}`, { method: 'POST' }); onChange() }
    catch (e) { setErr((e as Error).message) } finally { setBusy(null) }
  }
  const filing = d.kind === 'filing'
  const blocked = filing || !d.plan || d.plan.problems.length > 0 || !d.import_enabled
  const mb = (n: number) => t('{{v0}} MB', { v0: Math.round(n / 1e6).toLocaleString(locale()) })
  const overwrite = !d.replaces && d.import_enabled && d.plan?.overwritable && d.plan.existing
  return (
    <li className={`overflow-hidden rounded-xl border bg-surface ${d.users?.length ? 'border-user ring-1 ring-user' : 'border-line'}`}>
      <div className="flex items-start gap-2 px-4 py-2.5 text-sm" style={{ background: `color-mix(in srgb, ${k.color} 10%, transparent)` }}>
        <Icon size={16} aria-hidden className={`mt-0.5 shrink-0 ${filing ? 'animate-spin' : ''}`} style={{ color: k.color }} />
        <p className="min-w-0 break-words"><span className="font-semibold" style={{ color: k.color }}>{k.label}.</span> {d.why}</p>
      </div>
      <div className="p-4">
        <div className="flex gap-4">
          <Cover src={d.cover} alt={d.book_title} className="h-24 w-24 shrink-0 rounded-lg" />
          <Head d={d} />
        </div>
        {d.plan && d.plan.shown && (
          <p className="mt-3 break-words rounded-lg bg-surface-2 px-3 py-2 text-sm">
            <span className="text-muted">{d.replaces ? t('Ersetzt im Bestand:') : t('Zielordner:')}</span> {d.plan.shown}
            <span className="tabular text-muted"> · {d.plan.files.length} {d.plan.files.length === 1 ? t('Datei') : t('Dateien')}, {Math.round(d.plan.bytes / 1e6).toLocaleString(locale())} {t('MB')}</span>
          </p>
        )}
        {err && <p role="alert" className="mt-3 text-sm" style={{ color: 'var(--bad)' }}>{t('Das hat nicht geklappt.')}{' '}{err}</p>}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {d.replaces ? (
            <Button variant="primary" disabled={blocked || busy !== null}
              onClick={() => act('replace', t('«{{title}}» im Bestand ersetzen? Das bisherige Buch wandert in den Papierkorb, der neue Band kommt an dieselbe Stelle.', { title: d.book_title }), t('Bestand ersetzen'))}
              title={filing ? t('Wird gerade abgelegt') : blocked ? (d.plan?.problems[0] ?? t('Die Ablage ist ausgeschaltet')) : t('Das bisherige Buch geht in den Papierkorb, nichts wird gelöscht')}>
              {busy === 'replace' ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <FolderInput size={15} aria-hidden />}{t('Bestand ersetzen')}
            </Button>
          ) : overwrite ? (
            <Button disabled={filing || busy !== null} title={t('Der vorhandene Ordner geht in den Papierkorb, nichts wird gelöscht')}
              onClick={() => act('overwrite', t('Den vorhandenen Ordner «{{folder}}» überschreiben?\n\nIm Ordner liegen jetzt {{n}} Dateien ({{size}}). Sie wandern in den Papierkorb der Bibliothek, gelöscht wird nichts. An ihre Stelle kommt der neue Download mit {{m}} Dateien ({{size2}}).', { folder: d.plan!.shown, n: d.plan!.existing!.files, size: mb(d.plan!.existing!.bytes), m: d.plan!.files.length, size2: mb(d.plan!.bytes) }), t('Überschreiben'))}>
              {busy === 'overwrite' ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <FolderInput size={15} aria-hidden />}{t('Überschreiben')}
            </Button>
          ) : <Button variant={d.kind === 'ready' ? 'primary' : 'ghost'} disabled={blocked || busy !== null} onClick={() => act('import')}
            title={filing ? t('Wird gerade abgelegt') : blocked ? (d.plan?.problems[0] ?? t('Die Ablage ist ausgeschaltet')) : t('Kopiert in einen neuen Ordner; nichts wird überschrieben')}>
            {busy === 'import' ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <FolderInput size={15} aria-hidden />}
            {d.kind === 'ready' ? t('In Bibliothek ablegen') : t('Trotzdem ablegen')}
          </Button>}
          <Button disabled={filing || busy !== null} onClick={() => act('reject', t('«{{title}}» verwerfen? Der Download wird nicht abgelegt und dieser Treffer nicht mehr angeboten.', { title: d.book_title }), t('Verwerfen'))}>
            <Trash2 size={15} aria-hidden />{t('Verwerfen')}
          </Button>
          <Button disabled={filing || busy !== null} onClick={() => act('recheck')}><RotateCcw size={15} aria-hidden />{t('Neu prüfen')}</Button>
          <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="ml-auto inline-flex min-h-10 items-center gap-1 text-sm font-medium text-muted hover:text-text">
            <ChevronDown size={15} aria-hidden className={`transition-transform ${open ? 'rotate-180' : ''}`} />{t('Prüfpunkte')}
          </button>
        </div>
        {open && <div className="mt-3 border-t border-line pt-3"><Checks checks={d.checks} /></div>}
      </div>
    </li>
  )
}

function Row({ d, onChange }: { d: Dl; onChange?: () => void }) {
  const st = STATE[d.state] ?? STATE.queued
  const pct = Math.round((d.progress ?? 0) * 100)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const recheck = async () => {
    setBusy(true); setErr(null)
    try { await api(`/downloads/${d.id}/recheck`, { method: 'POST' }); onChange?.() } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  return (
    <li className={`flex gap-3 rounded-xl border bg-surface p-3 ${d.users?.length ? 'border-user' : 'border-line'}`}>
      <Cover src={d.cover} alt={d.book_title} className="h-16 w-16 shrink-0 rounded-lg" />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <Head d={d} />
          <Badge status={st.status} label={d.state === 'downloading' ? t('Lädt {{v0}} %', { v0: pct }) : d.state === 'rejected' && d.finding && FINDING[d.finding] ? FINDING[d.finding] : st.label} />
        </div>
        {d.state === 'downloading' && <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-2"><div className="h-full rounded-full transition-[width] duration-500" style={{ width: `${pct}%`, background: 'var(--load)' }} /></div>}
        {(d.state === 'failed' || d.state === 'rejected') && d.error && <p className="mt-1 break-words text-xs" style={{ color: 'var(--bad)' }}>{d.error}</p>}
        {d.state === 'split' && d.checks[0] && <p className="mt-1 break-words text-xs text-muted">{d.checks[0].text}</p>}
        {d.state === 'split' && d.children && d.children.length > 0 && (
          <div className="mt-1 text-xs"><p className="font-medium">{splitSummary(d.children)}</p>
            <ul className="mt-1 space-y-0.5">{d.children.map(k => <li key={k.id} className="flex gap-2"><span className="tabular text-faint">{t('Band')}{' '}{k.sequence}</span>
              <span className="min-w-0 flex-1 truncate">{k.title}</span>
              <span className={CHILD_BAD.has(k.state) ? '' : 'text-muted'} style={CHILD_BAD.has(k.state) ? { color: 'var(--bad)' } : undefined} title={k.error ?? undefined}>{(STATE[k.state] ?? STATE.queued).label}</span></li>)}</ul></div>
        )}
        {d.imported_to && <p className="mt-1 break-words text-xs text-muted">{t('Abgelegt in')}{' '}{d.imported_to.replace(/^\/library-(en|de)\//, '')}</p>}
        {d.state === 'rejected' && onChange && (
          <div className="mt-2"><Button disabled={busy} onClick={recheck} title={t('Noch einmal durch die Ankunftsprüfung, z. B. wenn es ein Sammelband war')}>
            {busy ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <RotateCcw size={15} aria-hidden />}{t('Neu prüfen')}</Button></div>
        )}
        {err && <p className="mt-1 text-xs" style={{ color: 'var(--bad)' }}>{err}</p>}
      </div>
    </li>
  )
}

const BUNDLE: Record<Bundle['state'], { status: 'downloading' | 'review' | 'failed' | 'owned'; label: string }> = {
  split: { status: 'downloading', label: t('Aufgeteilt, Bände in Prüfung') }, done: { status: 'owned', label: t('Erledigt') },
  partial: { status: 'failed', label: t('Nicht alle Bände abgelegt') }, unsafe: { status: 'review', label: t('Nicht sicher aufteilbar') },
  skipped: { status: 'review', label: t('Nicht aufgeteilt') },
}

/** Sammelband aus dem Bestand: Plan, Stand der Einzelbände und bei unsicheren Fällen der Grund. */
function BundleRow({ b, onChange }: { b: Bundle; onChange: () => void }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const st = BUNDLE[b.state]
  const retry = async () => {
    setBusy(true); setErr(null)
    try { await api(`/libsplit/${b.book_id}/retry`, { method: 'POST' }); onChange() } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  return (
    <li className="flex gap-3 rounded-xl border border-line bg-surface p-3">
      <Cover src={b.cover} alt={b.title} className="h-16 w-16 shrink-0 rounded-lg" />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <p className="flex items-center gap-2 text-sm"><LangTag lang={b.lang} /><span className="min-w-0 truncate text-muted">{b.rel_path.split('/')[0]}</span></p>
            <h3 className="mt-0.5 font-[family-name:var(--font-sans)] text-base font-semibold leading-snug tracking-normal">{b.title}</h3>
          </div>
          <Badge status={st.status} label={st.label} />
        </div>
        <p className="mt-1 break-words text-xs text-muted">{b.message}</p>
        {b.children.length > 0 && (
          <ul className="mt-2 space-y-0.5 text-xs">
            {b.children.map(k => <li key={k.id} className="flex gap-2"><span className="tabular text-faint">{t('Band')}{' '}{k.sequence}</span>
              <span className="min-w-0 flex-1 truncate">{k.title}</span><span className="text-muted">{(STATE[k.state] ?? STATE.queued).label}</span></li>)}
          </ul>
        )}
        <p className="tabular mt-1 text-xs text-faint">{fmtDateTime(b.at)}</p>
        {(b.state === 'unsafe' || b.state === 'skipped') && (
          <div className="mt-2"><Button disabled={busy} onClick={retry} title={t('Beim nächsten Lauf neu planen, z. B. nachdem Audible oder der Bestand sich geändert hat')}>
            {busy ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <RotateCcw size={15} aria-hidden />}{t('Neu prüfen')}</Button></div>
        )}
        {err && <p className="mt-1 text-xs" style={{ color: 'var(--bad)' }}>{err}</p>}
      </div>
    </li>
  )
}

function SideInfo({ s }: { s: Side }) {
  return (
    <div className="flex min-w-0 flex-1 basis-56 items-center gap-3">
      <Cover src={s.cover} alt={s.name} className="h-14 w-14 shrink-0 rounded-lg" />
      <div className="min-w-0">
        <p className="flex items-center gap-2"><LangTag lang={s.lang} />
          <Link to={`/serie/${s.series_id}`} className="min-w-0 truncate font-semibold hover:text-accent">{s.name}</Link></p>
        <p className="tabular mt-0.5 text-xs text-muted">{s.owned} {t('vorhanden ·')}{' '}{s.count} {t('Bände bei Audible ·')}{' '}{s.hours} h</p>
      </div>
    </div>
  )
}

/** Eine deutsche Fassung mit ihren wahrscheinlichsten englischen Gegenstücken; der beste Vorschlag steht oben. */
function PairCard({ pairs, onChange }: { pairs: Pair[]; onChange: () => void }) {
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const de = pairs[0].de
  const act = async (p: Pair, action: 'link' | 'reject') => {
    if (action === 'link' && !await ask(t('«{{v0}}» und «{{v1}}» auf eine Karte legen? Mit «Trennen» auf der Serienseite lässt sich das rückgängig machen.', { v0: p.de.name, v1: p.en.name }), t('Verknüpfen'))) return
    setBusy(`${action}-${p.en.edition_id}`); setErr(null)
    try { await api(`/pairs/${action}`, { method: 'POST', body: JSON.stringify({ de: p.de.edition_id, en: p.en.edition_id }) }); onChange() }
    catch (e) { setErr((e as Error).message) } finally { setBusy(null) }
  }
  return (
    <li className="rounded-xl border border-line bg-surface p-4">
      <SideInfo s={de} />
      <ul className="mt-3 space-y-2">
        {pairs.map(p => (
          <li key={p.en.edition_id} className="rounded-lg bg-surface-2 p-3">
            <div className="flex flex-wrap items-center gap-3">
              <SideInfo s={p.en} />
              <span className="tabular rounded-full px-2.5 py-1 text-xs font-semibold"
                style={{ color: p.score >= 70 ? 'var(--ok)' : 'var(--warn)', background: `color-mix(in srgb, ${p.score >= 70 ? 'var(--ok)' : 'var(--warn)'} 14%, transparent)` }}>
                {p.score} {t('Punkte')}
              </span>
            </div>
            <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-muted">{p.why.map(w => <li key={w}>{w}</li>)}</ul>
            <div className="mt-2 flex flex-wrap gap-2">
              <Button variant={p.rank === 0 ? 'primary' : 'ghost'} disabled={busy !== null} onClick={() => act(p, 'link')}>
                {busy === `link-${p.en.edition_id}` ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <Link2 size={15} aria-hidden />}{t('Verknüpfen')}
              </Button>
              <Button disabled={busy !== null} onClick={() => act(p, 'reject')} title={t('Dieses Paar nicht mehr vorschlagen')}>
                <X size={15} aria-hidden />{t('Passt nicht')}
              </Button>
            </div>
          </li>
        ))}
      </ul>
      {err && <p role="alert" className="mt-3 text-sm" style={{ color: 'var(--bad)' }}>{t('Das hat nicht geklappt.')}{' '}{err}</p>}
    </li>
  )
}

export default function ReviewPage() {
  const { data, error, reload } = useData<Review>('/review', 5000)
  // Register steht in der Adresse (?tab=), damit Zurück und Links aus Meldungen im richtigen Register landen
  const [params, setParams] = useSearchParams()
  const raw = params.get('tab')
  const tab: Tab = raw && raw in TABS ? raw as Tab : 'decide'
  const setTab = (tx: Tab) => setParams(tx === 'decide' ? {} : { tab: tx })
  const [pick, setPick] = useState<Unsure | null>(null)
  const pairs = useData<Pair[]>('/pairs', 0)
  const groups = (pairs.data ?? []).reduce<Pair[][]>((g, p) => {
    const last = g[g.length - 1]
    if (last && last[0].de.edition_id === p.de.edition_id) last.push(p); else g.push([p])
    return g
  }, [])
  const counts: Record<Tab, number> = { ...(data?.counts ?? { decide: 0, unsure: 0, active: 0, history: 0, bundles: 0 }), pairs: groups.length, manual: 0 }
  if (error && !data) return <Page title={t('Prüfen')}><ErrorBox message={error} onRetry={reload} /></Page>
  if (!data) return <Page title={t('Prüfen')}><div className="skeleton h-64 rounded-2xl" /></Page>
  return (
    <Page title={t('Prüfen')} subtitle={t('Was deine Entscheidung braucht, steht unter «Entscheiden» und «Treffer wählen». Der Rest läuft von selbst.')}>
      <Tabs label={t('Prüfen')} value={tab} onChange={setTab} items={(Object.keys(TABS) as Tab[]).map(tx => ({ key: tx, label: TABS[tx], count: counts[tx],
        tone: (tx === 'decide' && data.decide.some(d => d.users?.length)) || (tx === 'unsure' && data.unsure.some(u => u.users?.length)) ? 'user' as const : undefined }))} />
      {tab === 'decide' && (data.decide.length === 0
        ? <Empty title={t('Nichts zu entscheiden')} text={t('Angekommene Hörbücher erscheinen hier, sobald die Ankunftsprüfung fertig ist und sie nicht automatisch abgelegt wurden.')} />
        : <ul className="grid grid-cols-1 gap-4 xl:grid-cols-2">{data.decide.map(d => <DecideCard key={d.id} d={d} onChange={reload} />)}</ul>)}
      {tab === 'unsure' && (data.unsure.length === 0
        ? <Empty title={t('Keine offenen Treffer')} text={t('Findet die Automatik zu einem Band nur unsichere Treffer, steht er hier zur Auswahl von Hand.')} />
        : (
          <>
            <p className="mb-3 text-sm text-muted">{t('Zu diesen Bänden gibt es Treffer, aber keinen sicheren. Sieh sie dir an und starte den passenden selbst. Farbig umrandet und zuerst: Bücher, die Benutzer hinzugefügt haben.')}</p>
            <ul className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              {[...data.unsure].sort((a, b) => Number(!!b.users?.length) - Number(!!a.users?.length)).map(u => (
                <li key={u.book_id} className={`flex flex-wrap items-center gap-3 rounded-xl border bg-surface p-3 ${u.users?.length ? 'border-user ring-1 ring-user' : 'border-line'}`}>
                  <Cover src={u.cover} alt={u.title} className="h-16 w-16 shrink-0 rounded-lg" />
                  <div className="min-w-0 flex-1 basis-40">
                    <p className="flex flex-wrap items-center gap-2 text-sm"><LangTag lang={u.lang} /><UserTag users={u.users} />{u.series_id
                      ? <Link to={`/serie/${u.series_id}`} className="min-w-0 truncate text-muted hover:text-accent">{u.series}{u.sequence ? t(', Band {{v0}}', { v0: u.sequence }) : ''}</Link>
                      : <span className="min-w-0 truncate text-muted">{t('Einzelbuch')}</span>}</p>
                    <p className="truncate font-semibold">{u.title}</p>
                    <p className="tabular text-xs text-faint">{u.unsure} {t('unsichere Treffer · gesucht')}{' '}{fmtDateTime(u.last_search)}</p>
                  </div>
                  <Button onClick={() => setPick(u)}><Search size={15} aria-hidden />{t('Treffer ansehen')}</Button>
                </li>
              ))}
            </ul>
          </>
        ))}
      {tab === 'pairs' && (pairs.error && !pairs.data ? <ErrorBox message={pairs.error} onRetry={pairs.reload} />
        : !pairs.data ? <div className="skeleton h-64 rounded-2xl" />
        : groups.length === 0
        ? <Empty title={t('Keine offenen Vorschläge')} text={t('Hier erscheinen deutsche und englische Fassungen desselben Autors, die auf getrennten Karten liegen, aber wahrscheinlich dieselbe Serie sind.')} />
        : (
          <>
            <p className="mb-3 text-sm text-muted">{t('Diese Fassungen liegen auf getrennten Karten. Laufzeit und Bandzahl sprechen dafür, dass sie zusammengehören. Prüfe den Vorschlag und verknüpfe das richtige Paar.')}</p>
            <ul className="grid grid-cols-1 gap-4 xl:grid-cols-2">{groups.map(g => <PairCard key={g[0].de.edition_id} pairs={g} onChange={pairs.reload} />)}</ul>
          </>
        ))}
      {tab === 'bundles' && (data.bundles.length === 0
        ? <Empty title={t('Keine Sammelbände')} text={t('Sammelbände aus einer Datei im Bestand (Publisher\'s Pack, «Books 1-3») erscheinen hier, sobald ABW sie geprüft hat.')} />
        : (
          <>
            <p className="mb-3 flex gap-2 text-sm text-muted"><Scissors size={16} className="mt-0.5 shrink-0" aria-hidden />
              {t('Sammelbände aus einer Datei werden an den Kapitelgrenzen in Einzelbände geschnitten, wenn Kapiteltitel oder Laufzeiten laut Audible die Grenzen sicher zeigen. Sind alle Bände abgelegt, geht der Sammelband in den Papierkorb.')}</p>
            <ul className="grid grid-cols-1 gap-3 lg:grid-cols-2">{data.bundles.map(b => <BundleRow key={b.book_id} b={b} onChange={reload} />)}</ul>
          </>
        ))}
      {tab === 'active' && (data.active.length === 0
        ? <Empty title={t('Nichts unterwegs')} text={t('Laufende Downloads und Bücher, die gerade geprüft oder eingelesen werden, erscheinen hier.')} />
        : <ul className="grid grid-cols-1 gap-3 lg:grid-cols-2">{data.active.map(d => <Row key={d.id} d={d} />)}</ul>)}
      {tab === 'history' && (data.history.length === 0
        ? <Empty title={t('Noch kein Verlauf')} text={t('Abgelegte, defekte und verworfene Downloads werden hier aufgelistet.')} />
        : <ul className="grid grid-cols-1 gap-3 lg:grid-cols-2">{data.history.map(d => <Row key={d.id} d={d} onChange={reload} />)}</ul>)}
      {tab === 'manual' && <ManualImport />}
      {pick && (
        <SearchDialog books={[{ id: pick.book_id, title: pick.title, sequence: pick.sequence } as Book]} initial={pick.book_id}
          onClose={() => setPick(null)} onStarted={reload} />
      )}
    </Page>
  )
}
