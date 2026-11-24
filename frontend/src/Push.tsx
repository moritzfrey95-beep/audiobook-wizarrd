import { useEffect, useState } from 'react'
import { BellOff, BellRing, Send, Smartphone } from 'lucide-react'
import { api, fmtDate, useData } from './api'
import { Button } from './ui'
import { t } from './i18n'

interface PushState { key: string; devices: { endpoint: string; agent: string | null; created: string; last_ok: string | null }[] }
interface InstallPrompt extends Event { prompt: () => Promise<void>; userChoice: Promise<{ outcome: string }> }

// Chrome bietet die Installation nur einmal kurz nach dem Laden an: früh mitschreiben, Knopf erscheint später in den Einstellungen
let installPrompt: InstallPrompt | null = null
window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); installPrompt = e as InstallPrompt; window.dispatchEvent(new Event('abw-installable')) })

const supported = () => window.isSecureContext && 'serviceWorker' in navigator && t('PushManager') in window && t('Notification') in window
const standalone = () => window.matchMedia('(display-mode: standalone)').matches || (navigator as { standalone?: boolean }).standalone === true
const ios = () => /iphone|ipad|ipod/i.test(navigator.userAgent)

function keyBytes(b64: string) {
  const s = atob((b64 + '='.repeat((4 - b64.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/'))
  return Uint8Array.from(s, ch => ch.charCodeAt(0))
}

function deviceName(agent: string | null) {
  const a = agent ?? ''
  const os = /android/i.test(a) ? t('Android') : /iphone|ipad/i.test(a) ? 'iPhone/iPad' : /windows/i.test(a) ? t('Windows') : /mac os/i.test(a) ? t('Mac') : /linux/i.test(a) ? t('Linux') : t('Gerät')
  const br = /edg\//i.test(a) ? t('Edge') : /firefox/i.test(a) ? t('Firefox') : /chrome|crios/i.test(a) ? t('Chrome') : /safari/i.test(a) ? t('Safari') : ''
  return br ? t('{{v0}}, {{v1}}', { v0: os, v1: br }) : os
}

export function PushPanel({ box }: { box: string }) {
  const { data, reload } = useData<PushState>(supported() ? '/push' : null)
  const [endpoint, setEndpoint] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [canInstall, setCanInstall] = useState(!!installPrompt)

  useEffect(() => {
    const on = () => setCanInstall(true)
    window.addEventListener('abw-installable', on)
    if (supported()) navigator.serviceWorker.ready.then(r => r.pushManager.getSubscription()).then(s => setEndpoint(s?.endpoint ?? null)).catch(() => {})
    return () => window.removeEventListener('abw-installable', on)
  }, [])

  const run = async (fn: () => Promise<string>) => {
    setBusy(true); setMsg(null)
    try { setMsg({ ok: true, text: await fn() }); reload() } catch (e) { setMsg({ ok: false, text: (e as Error).message }) } finally { setBusy(false) }
  }
  const enable = () => run(async () => {
    if (await Notification.requestPermission() !== 'granted') throw new Error(t('Benachrichtigungen sind für ABW blockiert. Erlaube sie in den Website-Einstellungen des Browsers und versuche es noch einmal.'))
    const reg = await navigator.serviceWorker.ready
    let sub = await reg.pushManager.getSubscription()
    // Gerät war mit einem anderen Schlüssel angemeldet (z. B. neuer Datenordner): neu anmelden
    const key = keyBytes(data!.key)
    const old = sub?.options.applicationServerKey ? new Uint8Array(sub.options.applicationServerKey) : null
    if (sub && old && !(old.length === key.length && old.every((b, i) => b === key[i]))) {
      await sub.unsubscribe(); sub = null
    }
    sub ??= await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key })
    await api('/push/subscribe', { method: 'POST', body: JSON.stringify(sub.toJSON()) })
    setEndpoint(sub.endpoint)
    return t('Push ist auf diesem Gerät eingeschaltet. Mit «Testmeldung» prüfst du, ob sie ankommt.')
  })
  const disable = () => run(async () => {
    const sub = await (await navigator.serviceWorker.ready).pushManager.getSubscription()
    if (sub) { await api('/push/unsubscribe', { method: 'POST', body: JSON.stringify({ endpoint: sub.endpoint }) }); await sub.unsubscribe() }
    setEndpoint(null)
    return t('Push ist auf diesem Gerät ausgeschaltet.')
  })
  const test = () => run(async () => { await api('/push/test', { method: 'POST' }); return t('Testmeldung gesendet. Sie sollte in wenigen Sekunden auf allen eingeschalteten Geräten erscheinen.') })
  const install = async () => {
    if (!installPrompt) return
    await installPrompt.prompt()
    installPrompt = null; setCanInstall(false)
  }

  if (!supported()) {
    return (
      <div className={box}>
        <p className="text-sm text-muted">
          {!window.isSecureContext
            ? t('Push geht nur über die sichere Adresse (https), nicht über die IP-Adresse im Heimnetz. Öffne ABW über die https-Adresse und schalte Push dort ein.')
            : ios() && !standalone()
              ? t('Auf dem iPhone geht Push nur aus der installierten App: In Safari «Teilen», dann «Zum Home-Bildschirm», ABW von dort öffnen und hier einschalten.')
              : t('Dieser Browser kann keine Push-Meldungen empfangen.')}
        </p>
      </div>
    )
  }
  const here = !!endpoint && !!data?.devices.some(d => d.endpoint === endpoint)
  const denied = Notification.permission === 'denied'
  return (
    <div className={box}>
      <p className="mb-4 text-sm text-muted">
        {t('Was in der Glocke erscheint, kommt auf eingeschalteten Geräten auch als Benachrichtigung, wenn ABW geschlossen ist. Ein Tipp darauf öffnet die passende Seite. Kommen viele Meldungen auf einmal, gibt es eine Sammelmeldung.')}
        {!standalone() && t(' Auf dem Handy installierst du ABW am besten zuerst als App (Chrome: Menü, «App installieren»).')}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        {here
          ? <Button onClick={disable} disabled={busy}><BellOff size={15} aria-hidden />{t('Auf diesem Gerät ausschalten')}</Button>
          : <Button variant="primary" onClick={enable} disabled={busy || !data || denied}><BellRing size={15} aria-hidden />{t('Auf diesem Gerät einschalten')}</Button>}
        <Button onClick={test} disabled={busy || !data?.devices.length}><Send size={15} aria-hidden />{t('Testmeldung')}</Button>
        {canInstall && !standalone() && <Button onClick={install}><Smartphone size={15} aria-hidden />{t('Als App installieren')}</Button>}
      </div>
      {denied && <p role="alert" className="mt-3 text-sm" style={{ color: 'var(--bad)' }}>{t('Benachrichtigungen sind für ABW blockiert. Erlaube sie in den Website-Einstellungen des Browsers.')}</p>}
      {msg && <p role={msg.ok ? 'status' : 'alert'} className="mt-3 text-sm" style={{ color: msg.ok ? 'var(--ok)' : 'var(--bad)' }}>{msg.text}</p>}
      {!!data?.devices.length && (
        <ul className="mt-4 space-y-1 border-t border-line pt-4 text-sm">
          {data.devices.map(d => (
            <li key={d.endpoint} className="flex flex-wrap gap-x-2">
              <span className="font-medium">{deviceName(d.agent)}{d.endpoint === endpoint ? t(' (dieses Gerät)') : ''}</span>
              <span className="text-muted">{t('eingeschaltet')}{' '}{fmtDate(d.created)}{d.last_ok ? t(', zuletzt zugestellt {{v0}}', { v0: fmtDate(d.last_ok) }) : ''}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
