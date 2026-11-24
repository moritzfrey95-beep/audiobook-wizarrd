import { useState, type FormEvent } from 'react'
import { ArrowLeft, ArrowRight, Check, KeyRound, Wand2 } from 'lucide-react'
import { api, useData } from './api'
import { Button } from './ui'
import { GROUPS, GroupForm, type CfgView } from './Connections'
import { t } from './i18n'

export interface SetupState { needed: boolean; abs_ok: boolean; auth: boolean; auto_on: boolean }

const input = 'h-11 w-full rounded-lg border border-line bg-bg px-3 text-sm'

/** Erstes Konto: legt den Administrator an und meldet ihn an. Überspringen lässt ABW offen (nur im Heimnetz sinnvoll). */
function AccountStep({ onDone }: { onDone: () => void }) {
  const [user, setUser] = useState('admin')
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (pw !== pw2) { setMsg(t('Die beiden Passwörter sind nicht gleich')); return }
    setBusy(true); setMsg(null)
    try { await api('/auth/password', { method: 'POST', body: JSON.stringify({ user, password: pw }) }); onDone() }
    catch (err) { setMsg((err as Error).message) } finally { setBusy(false) }
  }
  return (
    <form onSubmit={submit} className="rounded-xl border border-line bg-surface p-4">
      <p className="mb-4 text-sm text-muted">{t('Mit einem Konto ist ABW nur nach Anmeldung erreichbar. Ohne Konto ist ABW für jeden im Heimnetz offen und von aussen gesperrt.')}</p>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <div><label htmlFor="w-user" className="mb-1 block text-sm font-semibold">{t('Benutzername')}</label><input id="w-user" className={input} value={user} onChange={e => setUser(e.target.value)} autoComplete="username" /></div>
        <div><label htmlFor="w-pw" className="mb-1 block text-sm font-semibold">{t('Passwort')}</label><input id="w-pw" type="password" className={input} value={pw} onChange={e => setPw(e.target.value)} autoComplete="new-password" /><p className="mt-1 text-xs text-muted">{t('Mindestens 10 Zeichen')}</p></div>
        <div><label htmlFor="w-pw2" className="mb-1 block text-sm font-semibold">{t('Passwort wiederholen')}</label><input id="w-pw2" type="password" className={input} value={pw2} onChange={e => setPw2(e.target.value)} autoComplete="new-password" /></div>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button type="submit" variant="primary" disabled={busy || !user.trim() || pw.length < 10}><KeyRound size={15} aria-hidden />{t('Konto anlegen')}</Button>
        {msg && <span role="status" className="text-sm text-bad">{msg}</span>}
      </div>
    </form>
  )
}

/** Setup-Wizard für eine neue Installation: Konto, Audiobookshelf (Pflicht), dann optionale Dienste. Die Automatik bleibt danach aus. */
export function SetupWizard({ state, onDone }: { state: SetupState; onDone: () => void }) {
  const cfg = useData<CfgView>('/config')
  const [setup, setSetup] = useState(state)
  const steps = ['welcome', ...(state.auth ? [] : ['account']), ...GROUPS.map(g => g.key), 'done']
  const [i, setI] = useState(0)
  const [err, setErr] = useState<string | null>(null)
  const step = steps[i]
  const group = GROUPS.find(g => g.key === step)
  const refresh = async () => setSetup(await api<SetupState>('/setup'))
  const next = () => setI(n => Math.min(n + 1, steps.length - 1))
  const finish = async () => {
    setErr(null)
    try { await api('/setup/done', { method: 'POST' }); onDone() } catch (e) { setErr((e as Error).message) }
  }
  const blocked = step === 'abs' && !setup.abs_ok
  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-10 sm:px-6">
      <div className="mb-6 flex items-center gap-3">
        <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent-strong text-on-accent"><Wand2 size={20} aria-hidden /></span>
        <div>
          <h1 className="font-display text-2xl font-semibold">{t('AudioBook Wizarrd einrichten')}</h1>
          <p className="text-sm text-muted">{t('Schritt')}{' '}{i + 1} {t('von')}{' '}{steps.length}</p>
        </div>
      </div>
      <ol className="mb-6 flex gap-1" aria-hidden>{steps.map((s, n) => <li key={s} className={`h-1.5 flex-1 rounded-full ${n <= i ? 'bg-accent' : 'bg-surface-2'}`} />)}</ol>

      {step === 'welcome' && (
        <div className="space-y-3 rounded-xl border border-line bg-surface p-5 text-sm">
          <p>{t('ABW zeigt deine Hörbuch-Serien aus Audiobookshelf mit allen Bänden laut Audible, sucht fehlende Bände über Prowlarr und legt sie nach einer Prüfung in die Bibliothek.')}</p>
          <p className="text-muted">{t('Pflicht ist nur Audiobookshelf. Alles andere kannst du überspringen und später unter Einstellungen, Verbindungen nachtragen. Nach der Einrichtung ist die Automatik aus, bis du sie einschaltest.')}</p>
          <p className="text-muted">{t('Ordner gibst du so an, wie ABW sie sieht. In Docker müssen sie in der Compose-Datei eingebunden sein.')}</p>
        </div>
      )}
      {step === 'account' && <AccountStep onDone={() => { refresh(); next() }} />}
      {group && (cfg.data
        ? <>
            <h2 className="mb-3 font-display text-lg font-semibold">{group.title}{group.optional && <span className="ml-2 text-sm font-normal text-muted">{t('optional')}</span>}</h2>
            <GroupForm key={group.key} group={group} cfg={cfg.data} saveLabel={t('Speichern')} onSaved={() => { cfg.reload(); refresh() }} />
          </>
        : <div className="skeleton h-64 rounded-2xl" />)}
      {step === 'done' && (
        <div className="space-y-3 rounded-xl border border-line bg-surface p-5 text-sm">
          <p className="font-semibold">{t('Fertig eingerichtet.')}</p>
          <p className="text-muted">{t('ABW liest jetzt den Bestand aus Audiobookshelf und die Serien bei Audible. Das dauert beim ersten Mal einige Minuten. Suche, Download und Ablage bleiben aus, bis du sie unter Werkstatt, Aufgaben einschaltest.')}</p>
          {err && <p role="alert" className="text-bad">{err}</p>}
        </div>
      )}

      <div className="mt-6 flex flex-wrap items-center justify-between gap-2">
        <Button onClick={() => setI(n => Math.max(0, n - 1))} disabled={i === 0}><ArrowLeft size={15} aria-hidden />{t('Zurück')}</Button>
        <div className="flex items-center gap-2">
          {blocked && <span className="text-sm text-muted">{t('Erst Audiobookshelf mit einer Bibliothek speichern')}</span>}
          {step === 'done'
            ? <Button variant="primary" onClick={finish}><Check size={15} aria-hidden />{t('ABW öffnen')}</Button>
            : <Button variant={group?.optional || step === 'account' ? 'ghost' : 'primary'} onClick={next} disabled={blocked}>
                {group?.optional || step === 'account' ? t('Weiter (überspringen, falls leer)') : t('Weiter')}<ArrowRight size={15} aria-hidden />
              </Button>}
        </div>
      </div>
    </main>
  )
}
