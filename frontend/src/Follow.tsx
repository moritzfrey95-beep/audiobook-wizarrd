import { useState } from 'react'
import { Bell, BellRing } from 'lucide-react'
import { api, useData } from './api'
import { Button } from './ui'
import { t } from './i18n'

/** Glocke: Serie oder Autor markieren. Zu Markiertem gibt es Benachrichtigungen (neuer Band, erschienen, im Bestand). */
export function FollowButton({ kind, id }: { kind: 'series' | 'author'; id: string | number }) {
  const { data, reload } = useData<{ series: number[]; authors: string[] }>('/follows')
  const [busy, setBusy] = useState(false)
  if (!data) return null
  const on = kind === 'series' ? data.series.includes(Number(id)) : data.authors.includes(String(id))
  const toggle = async () => {
    setBusy(true)
    try { await api('/follow', { method: 'POST', body: JSON.stringify({ kind, key: String(id), on: !on }) }); reload() } finally { setBusy(false) }
  }
  const what = kind === 'series' ? t('dieser Serie') : t('diesem Autor')
  return (
    <Button onClick={toggle} disabled={busy} title={on ? t('Du bekommst Benachrichtigungen zu {{v0}}. Klicken zum Abstellen.', { v0: what }) : t('Benachrichtigungen zu {{v0}} einschalten: neue Bände, erschienene Hörbücher, Neues im Bestand', { v0: what })}>
      {on ? <BellRing size={15} aria-hidden style={{ color: 'var(--accent)' }} /> : <Bell size={15} aria-hidden />}{on ? t('Markiert') : t('Markieren')}
    </Button>
  )
}
