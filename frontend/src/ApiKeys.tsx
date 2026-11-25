import { useState } from 'react'
import { KeyRound, Trash2 } from 'lucide-react'
import { api, fmtDate, useData } from './api'
import { Button, ErrorBox, ask } from './ui'
import { t } from './i18n'

interface Key { id: number; name: string; hint: string; created: string; last_used: string | null }

/** API-Schlüssel des Benutzers für eigene Skripte und andere Programme. Ein neuer Schlüssel ist nur einmal sichtbar. */
export function ApiKeys() {
  const { data, error, reload } = useData<Key[]>('/me/apikeys')
  const [keys, setKeys] = useState<Key[] | null>(null)
  const [name, setName] = useState('')
  const [fresh, setFresh] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const list = keys ?? data
  const create = async () => {
    setErr(null)
    try {
      const r = await api<{ key: string; keys: Key[] }>('/me/apikeys', { method: 'POST', body: JSON.stringify({ name }) })
      setFresh(r.key); setKeys(r.keys); setName('')
    } catch (e) { setErr((e as Error).message) }
  }
  const remove = async (k: Key) => {
    if (!await ask(t('Schlüssel «{{v0}}» löschen? Programme, die ihn nutzen, kommen danach nicht mehr hinein.', { v0: k.name }), t('Löschen'))) return
    setKeys(await api<Key[]>(`/me/apikeys/${k.id}`, { method: 'DELETE' }))
  }
  if (error && !list) return <ErrorBox message={error} onRetry={reload} />
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">{t('Für eigene Skripte und andere Programme: den Schlüssel im Header «X-Api-Key» (oder «Authorization: Bearer …») mitschicken. Er hat dieselben Rechte wie du.')}</p>
      {err && <ErrorBox message={err} plain />}
      {fresh && (
        <div className="rounded-lg border border-line bg-bg p-3">
          <p className="mb-1 text-sm font-semibold">{t('Neuer Schlüssel. Jetzt kopieren, er wird nicht noch einmal angezeigt:')}</p>
          <input readOnly value={fresh} onFocus={e => e.target.select()} aria-label={t('Neuer API-Schlüssel')} className="h-10 w-full rounded-lg border border-line bg-surface px-3 font-mono text-xs" />
        </div>
      )}
      {list && list.length > 0 && (
        <ul className="divide-y divide-line rounded-xl border border-line">
          {list.map(k => (
            <li key={k.id} className="flex items-center gap-3 p-2.5">
              <KeyRound size={16} aria-hidden className="shrink-0 text-faint" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{k.name} <span className="font-mono text-xs text-faint">…{k.hint}</span></p>
                <p className="text-xs text-muted">{t('Erstellt {{v0}}', { v0: fmtDate(k.created) })} · {k.last_used ? t('zuletzt benutzt {{v0}}', { v0: fmtDate(k.last_used) }) : t('noch nie benutzt')}</p>
              </div>
              <Button onClick={() => remove(k)} title={t('Schlüssel löschen')}><Trash2 size={15} aria-hidden /></Button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap gap-2">
        <input value={name} onChange={e => setName(e.target.value)} placeholder={t('Name, z. B. Home Assistant')} aria-label={t('Name des Schlüssels')}
          className="h-10 min-w-0 flex-1 rounded-lg border border-line bg-bg px-3 text-sm" />
        <Button onClick={create}><KeyRound size={15} aria-hidden />{t('Schlüssel erstellen')}</Button>
      </div>
    </div>
  )
}
