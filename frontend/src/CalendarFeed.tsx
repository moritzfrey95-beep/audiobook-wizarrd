import { useState } from 'react'
import { CalendarPlus, Check, Copy, RefreshCw } from 'lucide-react'
import { api, useData } from './api'
import { Button, Chip, ErrorBox, ask } from './ui'
import { t, uiLang } from './i18n'

/** Android kennt webcal:// nicht (Fehler ERR_UNKNOWN_URL_SCHEME), dort nur Google Kalender und Link kopieren. */
const ANDROID = /Android/i.test(navigator.userAgent)
const LINK = 'inline-flex min-h-10 items-center gap-1.5 rounded-lg px-3 text-sm font-semibold text-accent hover:bg-surface-2'

type Scope = 'followed' | 'monitored' | 'all'
type Lang = 'all' | 'de' | 'en'

/** Kalender-Abo: Link für Kalender-Apps, gefiltert nach Sprache und Umfang. Der Schlüssel im Link gilt nur für den Kalender. */
export function CalendarFeed() {
  const [open, setOpen] = useState(false)
  const { data, error, reload } = useData<{ token: string }>(open ? '/calendar/feed' : null)
  const [token, setToken] = useState<string | null>(null)
  const [scope, setScope] = useState<Scope>('followed')
  const [lang, setLang] = useState<Lang>('all')
  const [copied, setCopied] = useState(false)
  const tok = token ?? data?.token
  const url = tok ? `${window.location.origin}/api/calendar.ics?token=${encodeURIComponent(tok)}&lang=${lang}&scope=${scope}&ui=${uiLang()}` : ''
  const webcal = url.replace(/^https?:/, 'webcal:')
  const copy = async () => {
    try { await navigator.clipboard.writeText(url); setCopied(true); setTimeout(() => setCopied(false), 2000) } catch { /* Zwischenablage gesperrt: Feld markieren */ }
  }
  const renew = async () => {
    if (!await ask(t('Neuen Link erzeugen? Der bisherige Link hört sofort auf zu funktionieren, abonnierte Kalender bekommen nichts Neues mehr.'), t('Neuer Link'))) return
    setToken((await api<{ token: string }>('/calendar/feed', { method: 'POST' })).token)
  }
  if (!open) return <Button onClick={() => setOpen(true)}><CalendarPlus size={15} aria-hidden />{t('Kalender abonnieren')}</Button>
  return (
    <div className="mb-6 w-full rounded-xl border border-line bg-surface p-4">
      <h2 className="mb-1 font-semibold">{t('Kalender abonnieren')}</h2>
      <p className="mb-3 text-sm text-muted">{t('Den Link in Google Kalender, Apple Kalender, Outlook oder Thunderbird als Kalender-Abo («per URL») eintragen. Er zeigt Bände mit festem Termin und wird von der Kalender-App selbst aktualisiert.')}</p>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="w-20 text-sm text-muted">{t('Umfang')}</span>
        <Chip active={scope === 'followed'} onClick={() => setScope('followed')}>{t('Markierte Serien und Autoren')}</Chip>
        <Chip active={scope === 'monitored'} onClick={() => setScope('monitored')}>{t('Überwachte Fassungen')}</Chip>
        <Chip active={scope === 'all'} onClick={() => setScope('all')}>{t('Alle')}</Chip>
      </div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="w-20 text-sm text-muted">{t('Sprache')}</span>
        <Chip active={lang === 'all'} onClick={() => setLang('all')}>{t('Alle Sprachen')}</Chip>
        <Chip active={lang === 'de'} onClick={() => setLang('de')}>{t('Deutsch')}</Chip>
        <Chip active={lang === 'en'} onClick={() => setLang('en')}>{t('Englisch')}</Chip>
      </div>
      {error && !tok ? <ErrorBox message={error} onRetry={reload} plain /> : (
        <>
          <input readOnly value={url} onFocus={e => e.target.select()} aria-label={t('Link des Kalender-Abos')}
            className="h-10 w-full rounded-lg border border-line bg-bg px-3 font-mono text-xs" />
          <div className="mt-2 flex flex-wrap gap-2">
            <Button onClick={copy} disabled={!url}>{copied ? <Check size={15} aria-hidden /> : <Copy size={15} aria-hidden />}{copied ? t('Kopiert') : t('Link kopieren')}</Button>
            {url && !ANDROID && <a href={webcal} className={LINK}><CalendarPlus size={15} aria-hidden />{t('In Kalender-App öffnen')}</a>}
            {url && <a href={`https://calendar.google.com/calendar/r?cid=${encodeURIComponent(webcal)}`} target="_blank" rel="noreferrer" className={LINK}><CalendarPlus size={15} aria-hidden />{t('In Google Kalender')}</a>}
            <Button onClick={renew}><RefreshCw size={15} aria-hidden />{t('Neuer Link')}</Button>
          </div>
          <p className="mt-2 text-xs text-faint">{t('Wer den Link kennt, sieht deinen Kalender. Bei Bedarf einen neuen Link erzeugen.')}</p>
          {tok && (
            <p className="mt-3 border-t border-line pt-3 text-sm text-muted">
              {t('Als Feed: deine Meldungen (neu im Bestand, angekündigt, erschienen, Sprecher) für Feed-Reader.')}{' '}
              <a href={`${window.location.origin}/api/feed.atom?token=${encodeURIComponent(tok)}&ui=${uiLang()}`} className="font-semibold text-accent hover:underline">{t('Atom-Feed')}</a>
            </p>
          )}
        </>
      )}
    </div>
  )
}
