import { useState } from 'react'
import { Loader2, LogIn, WandSparkles } from 'lucide-react'
import { api } from '../api'
import { Button } from '../ui'
import { t } from '../i18n'

const field = 'h-11 w-full rounded-lg border border-line bg-surface px-3 text-sm'

/** Anmeldung: erscheint, sobald ein Passwort gesetzt und das Gerät nicht angemeldet ist. */
export default function LoginPage({ onDone }: { onDone: () => void }) {
  const [user, setUser] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submit = async (ev: React.FormEvent) => {
    ev.preventDefault(); setBusy(true); setError(null)
    try { await api('/auth/login', { method: 'POST', body: JSON.stringify({ user, password }) }); onDone() }
    catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-10">
      <form onSubmit={submit} className="w-full max-w-sm rounded-2xl border border-line bg-surface p-6">
        <div className="mb-5 flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-accent-strong text-on-accent"><WandSparkles size={20} aria-hidden /></span>
          <h1 className="font-[family-name:var(--font-display)] text-xl font-bold tracking-tight">{t('AudioBook Wizarrd')}</h1>
        </div>
        <label htmlFor="login-user" className="mb-1 block text-sm font-semibold">{t('Benutzername')}</label>
        <input id="login-user" className={field} value={user} onChange={e => setUser(e.target.value)} autoComplete="username" autoCapitalize="none" autoFocus required />
        <label htmlFor="login-password" className="mb-1 mt-4 block text-sm font-semibold">{t('Passwort')}</label>
        <input id="login-password" type="password" className={field} value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password" required />
        <p className="mt-3 text-xs text-muted">{t('Wer ein Konto in Audiobookshelf hat, meldet sich mit demselben Benutzernamen und Passwort an.')}</p>
        {error && <p role="alert" className="mt-3 text-sm" style={{ color: 'var(--bad)' }}>{error}</p>}
        <div className="mt-5 flex justify-end">
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <LogIn size={15} aria-hidden />}{t('Anmelden')}
          </Button>
        </div>
      </form>
    </main>
  )
}
