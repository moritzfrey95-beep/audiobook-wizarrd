import { useEffect, useState } from 'react'
import { Check, Copy, Loader2, Trash2, UserPlus, WandSparkles } from 'lucide-react'
import { api, fmtDate, useData } from './api'
import { Button, ErrorBox, ask } from './ui'
import { t } from './i18n'

interface Invite { code: string; name: string; expires: string; abw: string; abs: string }
interface Row { code: string; name: string; created: string; expires: string; opened: string | null; expired: boolean }

const link = (code: string) => `${window.location.origin}/einladung/${code}`

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <li className="rounded-2xl border border-line bg-surface p-5">
      <h2 className="mb-2 flex items-center gap-3 text-lg font-semibold">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent-strong text-sm font-bold text-on-accent">{n}</span>{title}
      </h2>
      <div className="space-y-3 text-sm text-muted">{children}</div>
    </li>
  )
}

function Qr({ code, what, url }: { code: string; what: 'abw' | 'abs'; url: string }) {
  if (!url) return null
  return (
    <div className="flex flex-wrap items-center gap-4">
      <img src={`/api/invite/${code}/qr.svg?what=${what}`} alt={t('QR-Code für {{v0}}', { v0: url })} className="h-36 w-36 rounded-lg bg-white p-1" />
      <a href={url} className="break-all font-mono text-sm text-accent hover:underline">{url}</a>
    </div>
  )
}

/** Öffentliche Seite für neue Mitglieder: drei Schritte, ohne Anmeldung lesbar. */
export function InvitePage({ code }: { code: string }) {
  const [inv, setInv] = useState<Invite | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { api<Invite>(`/invite/${encodeURIComponent(code)}`).then(setInv).catch(e => setErr((e as Error).message)) }, [code])
  return (
    <main className="mx-auto min-h-dvh max-w-2xl px-4 py-8 sm:py-12">
      <div className="mb-6 flex items-center gap-3">
        <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-accent-strong text-on-accent"><WandSparkles size={20} aria-hidden /></span>
        <span className="font-[family-name:var(--font-display)] text-xl font-bold">{t('AudioBook Wizarrd')}</span>
      </div>
      {err ? <ErrorBox message={err} plain /> : !inv ? <Loader2 className="animate-spin" aria-label={t('Lädt')} /> : (
        <>
          <h1 className="mb-2 font-[family-name:var(--font-display)] text-3xl font-bold">{t('Willkommen, {{v0}}!', { v0: inv.name })}</h1>
          <p className="mb-6 text-muted">{t('Du bist zu unserer Hörbuch-Sammlung eingeladen. In drei Schritten kannst du hören und dir Bücher wünschen.')}</p>
          <ol className="space-y-4">
            <Step n={1} title={t('App installieren')}>
              <p>{t('Gehört wird mit der App Absorb. Sie ist kostenlos und verbindet sich mit unserem Audiobookshelf-Server.')}</p>
              <p className="flex flex-wrap gap-3">
                <a className="font-semibold text-accent hover:underline" href="https://apps.apple.com/app/id6760673498">{t('iPhone (App Store)')}</a>
                <a className="font-semibold text-accent hover:underline" href="https://github.com/pounat/absorb/releases/latest">{t('Android (Download bei GitHub)')}</a>
              </p>
            </Step>
            <Step n={2} title={t('In der App anmelden')}>
              <p>{t('Trage in der App diese Server-Adresse ein und melde dich mit dem Benutzernamen und Passwort an, die du von uns bekommen hast:')}</p>
              {inv.abs ? <Qr code={code} what="abs" url={inv.abs} /> : <p>{t('Die Adresse bekommst du von uns.')}</p>}
            </Step>
            <Step n={3} title={t('Bücher wünschen')}>
              <p>{t('Im AudioBook Wizarrd siehst du alle Serien, was neu erscheint, und kannst dir Bücher wünschen. Die Anmeldung ist dieselbe wie in der App.')}</p>
              {inv.abw ? <Qr code={code} what="abw" url={inv.abw} /> : <p><a className="font-semibold text-accent hover:underline" href="/">{t('Zum AudioBook Wizarrd')}</a></p>}
              <p>{t('Tipp: Öffne ihn im Browser deines Handys und wähle «Zum Startbildschirm hinzufügen», dann ist er wie eine App da.')}</p>
            </Step>
          </ol>
          <p className="mt-6 text-xs text-faint">{t('Dieser Link gilt bis {{v0}}.', { v0: fmtDate(inv.expires) })}</p>
        </>
      )}
    </main>
  )
}

/** Einladungen anlegen und verwalten (Administrator). */
export function InvitesPanel() {
  const { data, error, reload } = useData<{ items: Row[]; abw: string; abs: string }>('/invites')
  const [name, setName] = useState('')
  const [copied, setCopied] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  if (error && !data) return <ErrorBox message={error} onRetry={reload} />
  if (!data) return <div className="skeleton h-32 rounded-xl" />
  const add = async () => {
    setErr(null)
    try { const r = await api<Invite>('/invites', { method: 'POST', body: JSON.stringify({ name }) }); setName(''); reload(); copy(r.code) }
    catch (e) { setErr((e as Error).message) }
  }
  const copy = async (code: string) => { try { await navigator.clipboard.writeText(link(code)); setCopied(code) } catch { setCopied(null) } }
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <p className="mb-3 text-sm text-muted">
        {t('Eine Einladung ist eine Seite mit drei Schritten (App, Anmelden, Wünsche) und QR-Codes. Sie legt kein Konto an: Das Konto legst du in Audiobookshelf an und übernimmst es oben. Gültig 30 Tage.')}
        {!data.abw && <> {t('Tipp: Unter Verbindungen die öffentliche Adresse von ABW eintragen, sonst fehlt der QR-Code dafür.')}</>}
      </p>
      <form className="flex max-w-md gap-2" onSubmit={e => { e.preventDefault(); if (name.trim()) add() }}>
        <input value={name} onChange={e => setName(e.target.value)} placeholder={t('Name, z. B. Oma')} aria-label={t('Name der eingeladenen Person')}
          className="min-h-10 w-full rounded-lg border border-line bg-surface-2 px-3 text-sm" />
        <Button type="submit" variant="primary" disabled={!name.trim()}><UserPlus size={15} aria-hidden />{t('Einladen')}</Button>
      </form>
      {err && <p role="status" className="mt-2 text-sm">{err}</p>}
      {data.items.length > 0 && (
        <ul className="mt-3 divide-y divide-line text-sm">
          {data.items.map(r => (
            <li key={r.code} className={`flex flex-wrap items-center gap-x-3 gap-y-1 py-2 ${r.expired ? 'opacity-55' : ''}`}>
              <span className="min-w-0 flex-1">
                <a href={link(r.code)} target="_blank" rel="noreferrer" className="font-medium hover:underline">{r.name}</a>
                <span className="block text-muted">{r.expired ? t('abgelaufen') : t('gültig bis {{v0}}', { v0: fmtDate(r.expires) })}{r.opened ? t(', geöffnet am {{v0}}', { v0: fmtDate(r.opened) }) : t(', noch nicht geöffnet')}</span>
              </span>
              <Button onClick={() => copy(r.code)}>{copied === r.code ? <Check size={14} aria-hidden /> : <Copy size={14} aria-hidden />}{copied === r.code ? t('Kopiert') : t('Link kopieren')}</Button>
              <Button onClick={async () => (await ask(t('Einladung für {{v0}} löschen? Der Link funktioniert dann nicht mehr.', { v0: r.name }), t('Löschen'))) && api(`/invites/${r.code}`, { method: 'DELETE' }).then(reload)}>
                <Trash2 size={14} aria-hidden />{t('Löschen')}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
