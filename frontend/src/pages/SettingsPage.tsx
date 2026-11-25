import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2, CircleSlash, Download, FileUp, KeyRound, Link2, LogOut, RefreshCw, Save, ShieldCheck, Trash2, Unlink, UserPlus, UserRound } from 'lucide-react'
import { api, fmtDate, useData } from '../api'
import { Button, ErrorBox, Page, Section, ask, prompt } from '../ui'
import { ApiKeys } from '../ApiKeys'
import { PushPanel } from '../Push'
import { GROUPS, GroupForm, type CfgView } from '../Connections'
import { t } from '../i18n'

interface Goodreads { user: string; shelf: string; added: number; owned: number; waiting: number; missing: { title: string; author: string | null }[] }
interface User { id: number; name: string; role: 'admin' | 'user'; created: string; goodreads: boolean; has_password: boolean; abs_user_id: string | null; abs_name: string | null; abs_active: boolean }
interface AbsUser { id: string; username: string; type: string; isActive: boolean; last_seen: string | null; own: boolean; linked: { id: number; name: string } | null; same_name: { id: number; name: string } | null; skip_all: boolean }
interface MeInfo { id: number; user: string | null; role: 'admin' | 'user'; auth_enabled: boolean; session_days: number; min_length: number; goodreads: Goodreads; abs_name: string | null; has_password: boolean; abs_url: string }
interface Settings { users: User[]; sources: { name: string; ok: boolean; hint: string }[] }

const field = 'h-11 w-full rounded-lg border border-line bg-bg px-3 text-sm'
const box = 'rounded-xl border border-line bg-surface p-4'

function Field({ id, label, hint, ...rest }: { id: string; label: string; hint?: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-sm font-semibold">{label}</label>
      <input id={id} className={field} {...rest} />
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  )
}

function AuthPanel({ me, onChange }: { me: MeInfo; onChange: () => void }) {
  const [user, setUser] = useState(me.user ?? '')
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [current, setCurrent] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const on = me.auth_enabled
  const admin = me.role === 'admin'
  const run = async (fn: () => Promise<unknown>, done: string, reloadPage = false) => {
    setBusy(true); setMsg(null)
    try { await fn(); setMsg({ ok: true, text: done }); setPassword(''); setRepeat(''); setCurrent(''); if (reloadPage) window.location.reload(); else onChange() }
    catch (e) { setMsg({ ok: false, text: (e as Error).message }) } finally { setBusy(false) }
  }
  const save = (ev: React.FormEvent) => {
    ev.preventDefault()
    if (password !== repeat) { setMsg({ ok: false, text: t('Die beiden Passwörter sind nicht gleich.') }); return }
    run(() => api('/auth/password', { method: 'POST', body: JSON.stringify({ user, password, current }) }),
      on ? t('Passwort geändert. Deine anderen Geräte müssen sich neu anmelden.') : t('Anmeldung eingeschaltet. Dieses Gerät ist angemeldet.'), !on)
  }
  const disable = async () => {
    if (!current) { setMsg({ ok: false, text: t('Zum Abschalten das bisherige Passwort eingeben.') }); return }
    if (!await ask(t('Anmeldung abschalten? ABW ist danach für jeden erreichbar, der die Adresse aufrufen kann.'), t('Abschalten'))) return
    run(() => api('/auth/disable', { method: 'POST', body: JSON.stringify({ current }) }), t('Anmeldung abgeschaltet.'), true)
  }
  const logout = async () => { await api('/auth/logout', { method: 'POST' }); window.location.reload() }
  if (on && me.abs_name && !me.has_password) return (
    <div className={box}>
      <p className="mb-4 text-sm text-muted">
        {t('Du bist als «')}{me.user}{t('» angemeldet (')}{admin ? t('Administrator') : t('Benutzer')}{t('), mit deinem Konto «')}{me.abs_name}{t('» aus Audiobookshelf. Es gilt dasselbe Passwort wie dort; ändern kannst du es in')}{' '}<a href={me.abs_url} target="_blank" rel="noreferrer" className="text-accent underline-offset-4 hover:underline">{t('Audiobookshelf')}</a>{t('. Ein Gerät bleibt')}{' '}{me.session_days} {t('Tage angemeldet.')}
      </p>
      <Button onClick={logout}><LogOut size={15} aria-hidden />{t('Abmelden')}</Button>
    </div>
  )
  return (
    <form onSubmit={save} className={box}>
      <p className="mb-4 text-sm text-muted">
        {on
          ? t('Du bist als «{{v0}}» angemeldet ({{v1}}). Ein Gerät bleibt {{v2}} Tage angemeldet.', { v0: me.user, v1: admin ? t('Administrator') : t('Benutzer'), v2: me.session_days })
            + (me.abs_name ? t(' Verknüpft mit dem Konto «{{v0}}» in Audiobookshelf: Dessen Benutzername und Passwort gelten hier ebenfalls, das Passwort unten zusätzlich.', { v0: me.abs_name }) : '')
          : t('Die Anmeldung ist ausgeschaltet: Jeder im Heimnetz und über Tailscale kann ABW öffnen. Lege ein Passwort fest, bevor du ABW von aussen erreichbar machst; damit wirst du Administrator und kannst weitere Benutzer anlegen.')}
      </p>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Field id="auth-user" label={t('Benutzername')} value={user} onChange={e => setUser(e.target.value)} autoComplete="username" autoCapitalize="none" required />
        {on && <Field id="auth-current" label={t('Bisheriges Passwort')} type="password" value={current} onChange={e => setCurrent(e.target.value)} autoComplete="current-password" />}
        <Field id="auth-new" label={on ? t('Neues Passwort') : t('Passwort')} type="password" value={password} onChange={e => setPassword(e.target.value)}
          autoComplete="new-password" minLength={me.min_length} required hint={t('Mindestens {{v0}} Zeichen.', { v0: me.min_length })} />
        <Field id="auth-repeat" label={t('Passwort wiederholen')} type="password" value={repeat} onChange={e => setRepeat(e.target.value)} autoComplete="new-password" required />
      </div>
      {msg && <p role={msg.ok ? 'status' : 'alert'} className="mt-3 text-sm" style={{ color: msg.ok ? 'var(--ok)' : 'var(--bad)' }}>{msg.text}</p>}
      <div className="mt-4 flex flex-wrap gap-2">
        <Button type="submit" variant="primary" disabled={busy}><KeyRound size={15} aria-hidden />{on ? t('Passwort ändern') : t('Anmeldung einschalten')}</Button>
        {on && <Button onClick={logout}><LogOut size={15} aria-hidden />{t('Abmelden')}</Button>}
        {on && admin && <Button onClick={disable} disabled={busy} title={t('Nur möglich, solange es keine weiteren Benutzer gibt')}>{t('Anmeldung abschalten')}</Button>}
      </div>
    </form>
  )
}

/** Nutzer aus Audiobookshelf übernehmen (nur Administrator): Konten als Zugänge anlegen oder mit bestehenden Zugängen verknüpfen. */
function AbsPanel({ users, onChange }: { users: User[]; onChange: () => void }) {
  const { data, error, reload } = useData<AbsUser[]>('/abs/users')
  const [target, setTarget] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const run = async (fn: () => Promise<string>) => {
    setBusy(true); setMsg(null)
    try { setMsg({ ok: true, text: await fn() }); reload(); onChange() } catch (e) { setMsg({ ok: false, text: (e as Error).message }) } finally { setBusy(false) }
  }
  const summary = (r: { created: string[]; linked: string[]; errors: string[] }) => {
    if (r.errors.length && !r.created.length && !r.linked.length) throw new Error(r.errors.join(' '))
    const parts = [
      r.created.length ? t('Angelegt: {{v0}}.', { v0: r.created.join(', ') }) : '',
      r.linked.length ? t('Verknüpft: {{v0}}.', { v0: r.linked.join(', ') }) : '',
      r.errors.length ? t('Nicht übernommen: {{v0}}', { v0: r.errors.join(' ') }) : '',
    ].filter(Boolean)
    return parts.length ? t('{{v0}} Der Hörfortschritt wird jetzt gelesen.', { v0: parts.join(' ') }) : t('Es gab nichts zu übernehmen.')
  }
  const importIds = (ids?: string[]) => run(async () =>
    summary(await api('/abs/users/import', { method: 'POST', body: JSON.stringify(ids ? { ids } : {}) })))
  const take = (a: AbsUser) => {
    const to = target[a.id] ?? (a.same_name ? String(a.same_name.id) : 'new')
    if (to === 'new') return importIds([a.id])
    const u = users.find(x => String(x.id) === to)
    return run(async () => {
      await api(`/users/${to}/abs`, { method: 'POST', body: JSON.stringify({ abs_user_id: a.id }) })
      return t('«{{v0}}» ist mit «{{v1}}» verknüpft. Benutzername und Passwort aus Audiobookshelf gelten jetzt auch hier.', { v0: u?.name, v1: a.username })
    })
  }
  const unlink = async (a: AbsUser) => {
    if (!a.linked) return
    if (!await ask(t('Verknüpfung von «{{v0}}» mit «{{v1}}» trennen? Das Audiobookshelf-Passwort gilt danach hier nicht mehr, der Hörfortschritt wird nicht mehr gezeigt.', { v0: a.linked.name, v1: a.username }), t('Trennen'))) return
    run(async () => { await api(`/users/${a.linked!.id}/abs`, { method: 'POST', body: JSON.stringify({}) }); return t('Verknüpfung getrennt.') })
  }
  if (error && !data) return <ErrorBox message={error} onRetry={reload} />
  if (!data) return <div className="skeleton h-40 rounded-xl" />
  const free = users.filter(u => !u.abs_user_id)
  const open = data.filter(a => a.isActive && !a.linked && !a.skip_all).length
  const own = data.find(a => a.skip_all)
  return (
    <div className={box}>
      <p className="mb-4 text-sm text-muted">
        {t('Wer in Audiobookshelf ein Konto hat, bekommt hier denselben Zugang: gleicher Benutzername, gleiches Passwort. ABW speichert das Passwort nicht, sondern fragt bei der Anmeldung Audiobookshelf. Übernommene Konten sind Benutzer (nicht Administrator), sehen bei jedem Buch ihren eigenen Hörfortschritt, und hat jemand ein Buch fertig gehört, werden Serie und Autor für ihn markiert.')}
        {own && t(' Das Konto «{{v0}}», mit dem ABW selbst liest, ist bei «Alle übernehmen» ausgenommen: Verknüpfe es mit deinem eigenen Zugang.', { v0: own.username })}
      </p>
      <ul className="divide-y divide-line">
        {data.map(a => (
          <li key={a.id} className="flex flex-wrap items-center gap-x-3 gap-y-2 py-2.5">
            <span className="min-w-0 flex-1 basis-40">
              <span className="block truncate text-sm font-semibold">{a.username}</span>
              <span className="block text-xs text-muted">
                {a.type === 'root' || a.type === 'admin' ? t('Administrator in Audiobookshelf') : t('Benutzer in Audiobookshelf')}
                {!a.isActive ? t(' · gesperrt') : ''}{a.last_seen ? t(' · zuletzt aktiv {{v0}}', { v0: fmtDate(a.last_seen) }) : t(' · noch nie angemeldet')}
              </span>
            </span>
            {a.linked ? (
              <span className="flex flex-wrap items-center gap-2">
                <span className="inline-flex items-center gap-1 text-sm" style={{ color: 'var(--ok)' }}><CheckCircle2 size={15} aria-hidden />{t('Verknüpft mit «')}{a.linked.name}»</span>
                <Button onClick={() => unlink(a)} disabled={busy}><Unlink size={14} aria-hidden />{t('Trennen')}</Button>
              </span>
            ) : (
              <span className="flex flex-wrap items-center gap-2">
                <label htmlFor={`abs-${a.id}`} className="sr-only">{t('Zugang für')}{' '}{a.username}</label>
                <select id={`abs-${a.id}`} className="h-9 rounded-lg border border-line bg-bg px-2 text-sm" disabled={busy}
                  value={target[a.id] ?? (a.same_name ? String(a.same_name.id) : 'new')} onChange={e => setTarget({ ...target, [a.id]: e.target.value })}>
                  {!a.same_name && <option value="new">{t('Neuen Zugang anlegen')}</option>}
                  {free.map(u => <option key={u.id} value={u.id}>{t('Mit «')}{u.name}{t('» verknüpfen')}</option>)}
                </select>
                <Button onClick={() => take(a)} disabled={busy || !a.isActive}><Link2 size={14} aria-hidden />{t('Übernehmen')}</Button>
              </span>
            )}
          </li>
        ))}
      </ul>
      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-4">
        <Button variant="primary" onClick={() => importIds()} disabled={busy || !open}><Download size={15} aria-hidden />{t('Alle übernehmen (')}{open})</Button>
      </div>
      {msg && <p role={msg.ok ? 'status' : 'alert'} className="mt-3 text-sm" style={{ color: msg.ok ? 'var(--ok)' : 'var(--bad)' }}>{msg.text}</p>}
    </div>
  )
}

type RecMode = 'admins' | 'all' | 'custom' | 'off'
interface RecRules { mode: RecMode; users: { id: number; name: string; role: 'admin' | 'user' }[]; allow: [number, number][] }

/** Wer wem empfehlen darf (nur Administrator): eine Grundregel oder eine eigene Tabelle «von → an». */
function RecRulesPanel() {
  const { data, error, reload } = useData<RecRules>('/recommend/rules')
  const [mode, setMode] = useState<RecMode | null>(null)
  const [allow, setAllow] = useState<Set<string> | null>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  if (error && !data) return <ErrorBox message={error} onRetry={reload} />
  if (!data) return <div className="skeleton h-32 rounded-xl" />
  const m = mode ?? data.mode
  const pairs = allow ?? new Set(data.allow.map(([a, b]) => `${a}:${b}`))
  const modes: { key: RecMode; label: string; hint: string }[] = [
    { key: 'admins', label: t('Nur an Administratoren'), hint: t('Administratoren empfehlen allen, Benutzer nur den Administratoren.') },
    { key: 'all', label: t('Alle an alle'), hint: t('Jeder kann jedem anderen Benutzer empfehlen.') },
    { key: 'custom', label: t('Eigene Auswahl'), hint: t('Du legst für jeden Benutzer fest, wem er empfehlen darf.') },
    { key: 'off', label: t('Aus'), hint: t('Niemand kann empfehlen.') },
  ]
  const toggle = (a: number, b: number) => {
    const k = `${a}:${b}`, n = new Set(pairs)
    if (n.has(k)) n.delete(k); else n.add(k)
    setAllow(n)
  }
  const save = async () => {
    setBusy(true); setMsg(null)
    try {
      await api('/recommend/rules', { method: 'PUT', body: JSON.stringify({ mode: m, allow: [...pairs].map(k => k.split(':').map(Number)) }) })
      setMode(null); setAllow(null); reload(); setMsg({ ok: true, text: t('Gespeichert.') })
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }) } finally { setBusy(false) }
  }
  return (
    <div className={box}>
      <fieldset>
        <legend className="mb-2 text-sm text-muted">{t('Empfohlene Serien erscheinen beim Empfänger unter «To Read», er entscheidet selbst, ob sie auf seine Liste kommen.')}</legend>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {modes.map(x => (
            <label key={x.key} className={`flex min-h-11 cursor-pointer items-start gap-3 rounded-lg border p-3 ${m === x.key ? 'border-accent bg-surface-2' : 'border-line'}`}>
              <input type="radio" name="rec-mode" value={x.key} checked={m === x.key} onChange={() => setMode(x.key)} className="mt-1 h-4 w-4 accent-[var(--accent)]" />
              <span><span className="block text-sm font-semibold">{x.label}</span><span className="block text-xs text-muted">{x.hint}</span></span>
            </label>
          ))}
        </div>
      </fieldset>
      {m === 'custom' && (
        <div className="mt-4 overflow-x-auto">
          <table className="text-sm">
            <caption className="mb-2 text-left text-xs text-muted">{t('Zeile = wer empfiehlt, Spalte = wer die Empfehlung bekommt')}</caption>
            <thead><tr><th className="p-2 text-left font-normal text-muted">{t('von ↓ / an →')}</th>{data.users.map(u => <th key={u.id} scope="col" className="p-2 font-semibold">{u.name}</th>)}</tr></thead>
            <tbody>
              {data.users.map(a => (
                <tr key={a.id} className="border-t border-line">
                  <th scope="row" className="p-2 text-left font-semibold">{a.name}</th>
                  {data.users.map(b => (
                    <td key={b.id} className="p-2 text-center">
                      {a.id === b.id ? <span className="text-faint">–</span>
                        : <input type="checkbox" aria-label={t('{{v0}} darf {{v1}} empfehlen', { v0: a.name, v1: b.name })} checked={pairs.has(`${a.id}:${b.id}`)}
                            onChange={() => toggle(a.id, b.id)} className="h-4 w-4 accent-[var(--accent)]" />}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button variant="primary" onClick={save} disabled={busy || (mode === null && allow === null)}><Save size={15} aria-hidden />{t('Speichern')}</Button>
        {msg && <span role="status" className={`text-sm ${msg.ok ? '' : 'text-bad'}`} style={msg.ok ? { color: 'var(--ok)' } : undefined}>{msg.text}</span>}
      </div>
    </div>
  )
}

/** Benutzerverwaltung (nur Administrator): Zugänge anlegen, Passwort neu setzen, Rolle ändern, löschen. */
function UsersPanel({ users, meId, minLength, onChange }: { users: User[]; meId: number; minLength: number; onChange: () => void }) {
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<'user' | 'admin'>('user')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const run = async (fn: () => Promise<unknown>, done: string) => {
    setBusy(true); setMsg(null)
    try { await fn(); setMsg({ ok: true, text: done }); onChange() } catch (e) { setMsg({ ok: false, text: (e as Error).message }) } finally { setBusy(false) }
  }
  const create = (ev: React.FormEvent) => {
    ev.preventDefault()
    run(async () => { await api('/users', { method: 'POST', body: JSON.stringify({ name, password, role }) }); setName(''); setPassword('') },
      t('Benutzer «{{v0}}» angelegt. Gib ihm Benutzername und Passwort weiter; das Passwort kann er unter Einstellungen selbst ändern.', { v0: name }))
  }
  const reset = async (u: User) => {
    const pw = await prompt(t('Neues Passwort für «{{v0}}» (mindestens {{v1}} Zeichen). Seine Geräte werden abgemeldet.', { v0: u.name, v1: minLength }))
    if (pw) run(() => api(`/users/${u.id}`, { method: 'POST', body: JSON.stringify({ password: pw }) }), t('Neues Passwort für «{{v0}}» gesetzt.', { v0: u.name }))
  }
  const flip = async (u: User) => {
    const to = u.role === 'admin' ? 'user' : 'admin'
    if (!await ask(to === 'admin' ? t('«{{v0}}» zum Administrator machen? Er darf dann alles, auch Prüfen, Aufgaben und Benutzer verwalten.', { v0: u.name }) : t('«{{v0}}» zum Benutzer machen? Er sieht dann «Prüfen» und «Aufgaben» nicht mehr.', { v0: u.name }), t('Rolle ändern'))) return
    run(() => api(`/users/${u.id}`, { method: 'POST', body: JSON.stringify({ role: to }) }), t('«{{v0}}» ist jetzt {{v1}}.', { v0: u.name, v1: to === 'admin' ? t('Administrator') : t('Benutzer') }))
  }
  const remove = async (u: User) => {
    if (!await ask(t('Benutzer «{{v0}}» löschen? Seine Markierungen und Benachrichtigungen gehen verloren. Was er hinzugefügt hat, bleibt in der Bibliothek.', { v0: u.name }), t('Löschen'))) return
    run(() => api(`/users/${u.id}`, { method: 'DELETE' }), t('Benutzer «{{v0}}» gelöscht.', { v0: u.name }))
  }
  return (
    <div className={box}>
      <p className="mb-4 text-sm text-muted">
        {t('Benutzer sehen Serien, Autoren, Entdecken, Kalender, Benachrichtigungen und Statistik. Sie können Titel hinzufügen, Serien und Autoren markieren, die automatische Suche für Serien und Bücher anstossen und ihr Goodreads-Regal verknüpfen. «Prüfen», «Aufgaben», die Schalter der Automatik und die Auswahl von Hand bleiben Administratoren vorbehalten.')}
      </p>
      <ul className="divide-y divide-line">
        {users.map(u => (
          <li key={u.id} className="flex flex-wrap items-center gap-x-3 gap-y-2 py-2.5">
            {u.role === 'admin' ? <ShieldCheck size={17} aria-hidden style={{ color: 'var(--accent)' }} /> : <UserRound size={17} aria-hidden className="text-muted" />}
            <span className="min-w-0 flex-1 basis-40">
              <span className="block truncate text-sm font-semibold">{u.name}{u.id === meId && <span className="font-normal text-muted"> {t('(du)')}</span>}</span>
              <span className="block text-xs text-muted">
                {u.role === 'admin' ? t('Administrator') : t('Benutzer')}{u.goodreads ? t(' · Goodreads verknüpft') : ''}
                {u.abs_user_id ? t(' · Audiobookshelf: {{v0}}{{v1}}{{v2}}', { v0: u.abs_name, v1: u.abs_active ? '' : t(' (dort gesperrt oder gelöscht)'), v2: u.has_password ? '' : t(', Passwort von dort') }) : ''}
              </span>
            </span>
            {u.id !== meId && (
              <span className="flex flex-wrap gap-2">
                <Button onClick={() => reset(u)} disabled={busy}><KeyRound size={14} aria-hidden />{t('Passwort setzen')}</Button>
                <Button onClick={() => flip(u)} disabled={busy}>{u.role === 'admin' ? t('Zum Benutzer machen') : t('Zum Administrator machen')}</Button>
                <Button onClick={() => remove(u)} disabled={busy}><Trash2 size={14} aria-hidden />{t('Löschen')}</Button>
              </span>
            )}
          </li>
        ))}
      </ul>
      <form onSubmit={create} className="mt-4 grid grid-cols-1 items-end gap-4 border-t border-line pt-4 md:grid-cols-[1fr_1fr_auto_auto]">
        <Field id="new-user" label={t('Neuer Benutzer')} value={name} onChange={e => setName(e.target.value)} autoComplete="off" autoCapitalize="none" required placeholder={t('Benutzername')} />
        <Field id="new-pass" label={t('Passwort')} type="text" value={password} onChange={e => setPassword(e.target.value)} autoComplete="off" minLength={minLength} required placeholder={t('Mindestens {{v0}} Zeichen', { v0: minLength })} />
        <div>
          <label htmlFor="new-role" className="mb-1 block text-sm font-semibold">{t('Rolle')}</label>
          <select id="new-role" value={role} onChange={e => setRole(e.target.value as 'user' | 'admin')} className={field}>
            <option value="user">{t('Benutzer')}</option><option value="admin">{t('Administrator')}</option>
          </select>
        </div>
        <Button type="submit" variant="primary" disabled={busy}><UserPlus size={15} aria-hidden />{t('Benutzer anlegen')}</Button>
      </form>
      {msg && <p role={msg.ok ? 'status' : 'alert'} className="mt-3 text-sm" style={{ color: msg.ok ? 'var(--ok)' : 'var(--bad)' }}>{msg.text}</p>}
    </div>
  )
}

function GoodreadsPanel({ g, onChange }: { g: Goodreads; onChange: () => void }) {
  const [user, setUser] = useState(g.user)
  const [shelf, setShelf] = useState(g.shelf)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const file = useRef<HTMLInputElement>(null)
  const run = async (fn: () => Promise<string>) => {
    setBusy(true); setMsg(null)
    try { setMsg({ ok: true, text: await fn() }); onChange() } catch (e) { setMsg({ ok: false, text: (e as Error).message }) } finally { setBusy(false) }
  }
  const save = (ev: React.FormEvent) => {
    ev.preventDefault()
    run(async () => {
      await api('/me/goodreads', { method: 'POST', body: JSON.stringify({ user, shelf }) })
      return user.trim() ? t('Gespeichert. Der Abgleich läuft im Hintergrund; Treffer kommen als fehlend in die Bibliothek und werden gesucht.') : t('Goodreads-Regal entfernt.')
    })
  }
  const upload = (f: File | undefined) => {
    if (!f) return
    run(async () => {
      const r = await api<{ total: number; new: number }>('/me/goodreads/csv', { method: 'POST', body: JSON.stringify({ text: await f.text() }) })
      return t('{{v0}} Bücher im Regal «{{v1}}» gefunden, {{v2}} neu vorgemerkt. Der Abgleich mit Audible läuft im Hintergrund.', { v0: r.total, v1: shelf, v2: r.new })
    })
    if (file.current) file.current.value = ''
  }
  return (
    <div className={box}>
      <p className="mb-4 text-sm text-muted">
        {t('ABW liest die 100 zuletzt hinzugefügten Bücher deines öffentlichen Goodreads-Regals und sucht sie bei Audible. Treffer kommen als fehlend in die Bibliothek und werden mit Vorrang gesucht und geladen. Gehört ein Buch zu einer Serie, kommt die ganze Serie mit. Für längere Listen liest du einmal den Export ein (Goodreads: «My Books», «Import and export», «Export Library»).')}
      </p>
      <form onSubmit={save} className="grid grid-cols-1 items-end gap-4 md:grid-cols-[2fr_1fr_auto]">
        <Field id="gr-user" label={t('Benutzernummer oder Profil-Adresse')} value={user} onChange={e => setUser(e.target.value)}
          placeholder="https://www.goodreads.com/user/show/12345-name" hint={t('Leer lassen und speichern, um das Regal zu entfernen.')} />
        <Field id="gr-shelf" label={t('Regal')} value={shelf} onChange={e => setShelf(e.target.value)} placeholder="to-read" hint={t('Name des Regals bei Goodreads.')} />
        <div className="pb-5"><Button type="submit" variant="primary" disabled={busy}><Save size={15} aria-hidden />{t('Speichern')}</Button></div>
      </form>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <input ref={file} id="gr-file" type="file" accept=".csv,text/csv" className="sr-only" onChange={e => upload(e.target.files?.[0])} />
        <Button onClick={() => file.current?.click()} disabled={busy}><FileUp size={15} aria-hidden />{t('Export einlesen (CSV)')}</Button>
        <Button onClick={() => run(async () => { await api('/me/goodreads/sync', { method: 'POST' }); return t('Der Abgleich ist gestartet.') })} disabled={busy || (!g.user && !g.waiting)}>
          <RefreshCw size={15} aria-hidden />{t('Jetzt abgleichen')}
        </Button>
      </div>
      {msg && <p role={msg.ok ? 'status' : 'alert'} className="mt-3 text-sm" style={{ color: msg.ok ? 'var(--ok)' : 'var(--bad)' }}>{msg.text}</p>}
      <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-line pt-4 text-sm sm:grid-cols-4">
        <div><dt className="text-faint">{t('In die Bibliothek aufgenommen')}</dt><dd className="tabular text-xl font-bold">{g.added}</dd></div>
        <div><dt className="text-faint">{t('Schon im Bestand')}</dt><dd className="tabular text-xl font-bold">{g.owned}</dd></div>
        <div><dt className="text-faint">{t('Warten auf den Abgleich')}</dt><dd className="tabular text-xl font-bold">{g.waiting}</dd></div>
        <div><dt className="text-faint">{t('Bei Audible nicht gefunden')}</dt><dd className="tabular text-xl font-bold">{g.missing.length}</dd></div>
      </dl>
      {g.missing.length > 0 && (
        <details className="mt-3 text-sm">
          <summary className="min-h-9 cursor-pointer font-medium text-muted hover:text-text">{t('Nicht gefundene Bücher zeigen')}</summary>
          <ul className="mt-2 max-h-64 space-y-1 overflow-y-auto text-muted">
            {g.missing.map((m, i) => <li key={i}>{m.title}{m.author ? t(', {{v0}}', { v0: m.author }) : ''}</li>)}
          </ul>
        </details>
      )}
    </div>
  )
}

/** Verbindungen zu Audiobookshelf, Prowlarr, Download-Programmen usw. Je Abschnitt aufklappbar. */
function ConnectionsSection({ onChange }: { onChange: () => void }) {
  const cfg = useData<CfgView>('/config')
  const [open, setOpen] = useState<string | null>(null)
  if (!cfg.data) return cfg.error ? <div className="mt-8"><ErrorBox message={cfg.error} onRetry={cfg.reload} /></div> : null
  const c = cfg.data
  const filled = (g: typeof GROUPS[number]) => g.fields.some(f => c.fields[f.name]?.source !== 'default' && (c.fields[f.name]?.value || c.fields[f.name]?.set))
  return (
    <Section title={t('Verbindungen')} hint={t('Gespeicherte Werte gehen vor der Datei .env')}>
      <div className="space-y-3">
        {GROUPS.map(g => (
          <div key={g.key}>
            <button type="button" onClick={() => setOpen(open === g.key ? null : g.key)} aria-expanded={open === g.key}
              className="flex min-h-11 w-full items-center justify-between rounded-xl border border-line bg-surface px-4 text-left text-sm font-semibold hover:bg-surface-2">
              <span>{g.title}</span>
              <span className="text-xs font-normal text-muted">{g.key === 'folders' ? (filled(g) ? t('angepasst') : t('Grundschema')) : filled(g) ? t('eingerichtet') : g.optional ? t('optional, nicht eingerichtet') : t('nicht eingerichtet')}</span>
            </button>
            {open === g.key && <div className="mt-2"><GroupForm group={g} cfg={c} onSaved={() => { cfg.reload(); onChange() }} /></div>}
          </div>
        ))}
        {!c.speech && <p className="text-sm text-muted">{t('Spracherkennung der Ansage ist nicht installiert (schlankes Image). Mit ABW_WHISPER=1 bauen, um sie zu nutzen.')}</p>}
      </div>
    </Section>
  )
}

export default function SettingsPage() {
  const me = useData<MeInfo>('/me')
  const admin = me.data?.role === 'admin'
  const { data, error, reload } = useData<Settings>(admin ? '/settings' : null)
  if (me.error && !me.data) return <Page title={t('Einstellungen')}><ErrorBox message={me.error} onRetry={me.reload} /></Page>
  if (!me.data) return <Page title={t('Einstellungen')}><div className="skeleton h-64 rounded-2xl" /></Page>
  const m = me.data
  return (
    <Page title={t('Einstellungen')} subtitle={admin
      ? <>{t('Anmeldung, Benutzer, Goodreads und Stand der Quellen. Die Schalter der Automatik stehen unter Werkstatt,')}{' '}<Link to="/aufgaben" className="text-accent underline-offset-4 hover:underline">{t('Aufgaben')}</Link>.</>
      : t('Dein Passwort, Push aufs Handy und dein Goodreads-Regal')}>
      <Section title={m.auth_enabled ? t('Dein Zugang') : t('Anmeldung')}><AuthPanel key={String(m.auth_enabled) + m.user} me={m} onChange={me.reload} /></Section>
      {admin && m.auth_enabled && data && (
        <Section title={t('Benutzer')} hint={`${data.users.length} ${data.users.length === 1 ? t('Zugang') : t('Zugänge')}`}>
          <UsersPanel users={data.users} meId={m.id} minLength={m.min_length} onChange={reload} />
        </Section>
      )}
      {admin && m.auth_enabled && data && data.users.length > 1 && (
        <Section title={t('Weiterempfehlen')} hint={t('Wer wem Serien empfehlen darf')}>
          <RecRulesPanel />
        </Section>
      )}
      {admin && m.auth_enabled && data && (
        <Section title={t('Nutzer aus Audiobookshelf')} hint={t('Gleicher Benutzername, gleiches Passwort')}>
          <AbsPanel users={data.users} onChange={reload} />
        </Section>
      )}
      <Section title={t('Push aufs Handy')}><PushPanel box={box} /></Section>
      <Section title={t('Goodreads verknüpfen')}><GoodreadsPanel key={m.goodreads.user + m.goodreads.shelf} g={m.goodreads} onChange={me.reload} /></Section>
      <Section title={t('API-Schlüssel')} hint={t('Zugang für Skripte und andere Programme')}><ApiKeys /></Section>
      {admin && error && !data && <div className="mt-8"><ErrorBox message={error} onRetry={reload} /></div>}
      {admin && <ConnectionsSection onChange={reload} />}
      {admin && data && (
        <Section title={t('Stand der Quellen und Dienste')}>
          <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {data.sources.map(s => (
              <li key={s.name} className={`${box} flex items-start gap-3`}>
                {s.ok ? <CheckCircle2 size={18} className="mt-0.5 shrink-0" style={{ color: 'var(--ok)' }} aria-hidden /> : <CircleSlash size={18} className="mt-0.5 shrink-0 text-faint" aria-hidden />}
                <div>
                  <p className="text-sm font-semibold">{s.name} <span className="font-normal text-muted">({s.ok ? t('eingerichtet') : t('nicht eingerichtet')})</span></p>
                  <p className="text-sm text-muted">{s.hint}</p>
                </div>
              </li>
            ))}
          </ul>
        </Section>
      )}
    </Page>
  )
}
