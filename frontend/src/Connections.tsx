import { useState } from 'react'
import { CheckCircle2, CircleAlert, Loader2, PlugZap, Save } from 'lucide-react'
import { api } from './api'
import { Button } from './ui'
import { t } from './i18n'

export interface CfgField { name: string; kind: string; group: string; source: 'db' | 'env' | 'default'; value: string; set?: boolean }
export interface CfgView { fields: Record<string, CfgField>; langs: ('en' | 'de')[]; speech: boolean; plugins: string[] }
interface TestResult { ok: boolean; text: string; libraries?: { id: string; name: string; folders: string[] }[]; indexers?: Indexer[] }
interface Indexer { id: number; name: string; protocol: string | null; enabled: boolean }

type Def = { name: string; label: string; hint?: string; type?: 'text' | 'password' | 'checkbox' }
export interface Group { key: string; title: string; text: string; test?: string; fields: Def[]; optional?: boolean }

/** Verbindungen in der Reihenfolge des Wizards. Ordner sind immer Pfade aus Sicht von ABW (im Container). */
export const GROUPS: Group[] = [
  {
    key: 'abs', title: t('Audiobookshelf'), test: 'abs', text: t('Pflicht. ABW liest Bestand, Benutzer und Hörfortschritt aus Audiobookshelf und legt neue Bücher dort ab.'),
    fields: [
      { name: 'ABS_URL', label: t('Adresse'), hint: t('z. B. http://audiobookshelf:80') },
      { name: 'ABS_API_KEY', label: t('API-Schlüssel'), type: 'password', hint: t('Audiobookshelf: Einstellungen, API-Schlüssel') },
      { name: 'ABS_PUBLIC_URL', label: t('Adresse für Links (optional)'), hint: t('Wie du Audiobookshelf im Browser öffnest') },
      { name: 'ABS_LIBRARY_EN', label: t('Bibliothek Englisch (ID)'), hint: t('Leer lassen, wenn es keine englischen Hörbücher gibt') },
      { name: 'ABS_LIBRARY_DE', label: t('Bibliothek Deutsch (ID)'), hint: t('Leer lassen, wenn es keine deutschen Hörbücher gibt') },
      { name: 'LIBRARY_EN_ROOT', label: t('Ordner der englischen Bibliothek'), hint: t('Pfad in ABW, auf denselben Ordner wie in Audiobookshelf eingebunden') },
      { name: 'LIBRARY_DE_ROOT', label: t('Ordner der deutschen Bibliothek') },
      { name: 'LIBRARY_TRASH', label: t('Papierkorb'), hint: t('Auf derselben Platte wie die Bibliotheken') },
      { name: 'IMPORT_ENABLED', label: t('ABW darf Bücher in die Bibliothek legen'), type: 'checkbox' },
    ],
  },
  {
    key: 'prowlarr', title: t('Prowlarr'), test: 'prowlarr', optional: true, text: t('Suche nach Hörbüchern und E-Books. Usenet-Indexer laden über SABnzbd, Torrent-Indexer über qBittorrent.'),
    fields: [
      { name: 'PROWLARR_URL', label: t('Adresse'), hint: t('z. B. http://prowlarr:9696') },
      { name: 'PROWLARR_API_KEY', label: t('API-Schlüssel'), type: 'password' },
      { name: 'USENET_INDEXER_IDS', label: t('Usenet-Indexer (IDs)'), hint: t('Nach dem Test auswählen oder Zahlen mit Komma') },
      { name: 'TORRENT_INDEXER_IDS', label: t('Torrent-Indexer für Hörbücher (IDs)') },
      { name: 'EBOOK_TORRENT_INDEXER_IDS', label: t('Torrent-Indexer für E-Books (IDs)') },
    ],
  },
  {
    key: 'sabnzbd', title: 'SABnzbd', test: 'sabnzbd', optional: true, text: t('Download aus dem Usenet. Programme mit derselben Schnittstelle gehen auch.'),
    fields: [
      { name: 'SAB_URL', label: t('Adresse'), hint: t('z. B. http://sabnzbd:8080') },
      { name: 'SABNZBD_API_KEY', label: t('API-Schlüssel'), type: 'password' },
      { name: 'SAB_CATEGORY', label: t('Kategorie') },
      { name: 'USENET_COMPLETE_ROOT', label: t('Ordner der fertigen Downloads'), hint: t('Pfad in ABW') },
    ],
  },
  {
    key: 'qbit', title: 'qBittorrent', test: 'qbit', optional: true, text: t('Download über Torrent, in einen eigenen Ordner.'),
    fields: [
      { name: 'QBIT_URL', label: t('Adresse'), hint: t('z. B. http://qbittorrent:8080') },
      { name: 'QBIT_USERNAME', label: t('Benutzer'), type: 'password' },
      { name: 'QBIT_PASSWORD', label: t('Passwort'), type: 'password' },
      { name: 'QBIT_CATEGORY', label: t('Kategorie') },
      { name: 'QBIT_SAVE_ROOT', label: t('Speicherordner aus Sicht von qBittorrent') },
      { name: 'TORRENT_COMPLETE_ROOT', label: t('Derselbe Ordner aus Sicht von ABW') },
    ],
  },
  {
    key: 'notify', title: t('Meldungen'), test: 'notify', optional: true, text: t('Meldungen über ntfy. Push im Browser geht auch ohne.'),
    fields: [
      { name: 'NTFY_URL', label: 'ntfy-Adresse' },
      { name: 'NTFY_TOPIC', label: t('Thema') },
      { name: 'NTFY_TOKEN', label: t('Zugangsschlüssel'), type: 'password' },
      { name: 'PUBLIC_URL', label: t('Adresse von ABW für Links in Meldungen') },
    ],
  },
  {
    key: 'extra', title: t('Weiteres'), test: 'extra', optional: true, text: t('FlareSolverr für Genres von Goodreads und für Erweiterungen.'),
    fields: [
      { name: 'FLARESOLVERR_URL', label: t('FlareSolverr-Adresse'), hint: t('z. B. http://flaresolverr:8191/v1') },
      { name: 'WHISPER_MODEL', label: t('Whisper-Modell'), hint: t('Nur im Image mit Spracherkennung') },
    ],
  },
]

const input = 'h-10 w-full rounded-lg border border-line bg-bg px-3 text-sm'
const PATHS = new Set(['LIBRARY_EN_ROOT', 'LIBRARY_DE_ROOT', 'LIBRARY_TRASH', 'USENET_COMPLETE_ROOT', 'TORRENT_COMPLETE_ROOT'])

/** Ein Abschnitt Verbindungen: Felder, Test, Speichern. onSaved bekommt den neuen Stand. */
export function GroupForm({ group, cfg, onSaved, saveLabel = t('Speichern') }: { group: Group; cfg: CfgView; onSaved: (c: CfgView) => void; saveLabel?: string }) {
  const start = Object.fromEntries(group.fields.map(f => [f.name, cfg.fields[f.name]?.value ?? '']))
  const [vals, setVals] = useState<Record<string, string>>(start)
  const [busy, setBusy] = useState<string | null>(null)
  const [result, setResult] = useState<TestResult | null>(null)
  const [paths, setPaths] = useState<Record<string, TestResult>>({})
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const set = (k: string, v: string) => setVals(o => ({ ...o, [k]: v }))
  const ids = (k: string) => new Set((vals[k] ?? '').split(',').map(x => x.trim()).filter(Boolean))
  const toggle = (k: string, id: number) => { const s = ids(k); if (s.has(String(id))) s.delete(String(id)); else s.add(String(id)); set(k, [...s].join(',')) }

  const test = async () => {
    setBusy('test'); setMsg(null)
    try {
      setResult(await api<TestResult>('/config/test', { method: 'POST', body: JSON.stringify({ service: group.test, values: vals }) }))
      for (const f of group.fields.filter(f => PATHS.has(f.name) && vals[f.name])) {
        const r = await api<TestResult>('/config/test', { method: 'POST', body: JSON.stringify({ service: 'path:' + f.name, values: vals }) })
        setPaths(p => ({ ...p, [f.name]: r }))
      }
    } catch (e) { setResult({ ok: false, text: (e as Error).message }) } finally { setBusy(null) }
  }
  const save = async () => {
    setBusy('save'); setMsg(null)
    try {
      const c = await api<CfgView>('/config', { method: 'PUT', body: JSON.stringify({ values: vals }) })
      setMsg({ ok: true, text: t('Gespeichert') }); onSaved(c)
      setVals(v => Object.fromEntries(Object.entries(v).map(([k, x]) => [k, c.fields[k]?.kind === 'secret' ? '' : x])))
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }) } finally { setBusy(null) }
  }

  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <p className="mb-4 text-sm text-muted">{group.text}</p>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {group.fields.map(f => {
          const meta = cfg.fields[f.name]
          const id = `cfg-${f.name}`
          if (f.type === 'checkbox') return (
            <label key={f.name} className="flex min-h-10 items-center gap-2 text-sm md:col-span-2">
              <input type="checkbox" checked={['1', 'true', 'ja', 'yes', 'on'].includes((vals[f.name] ?? '').toLowerCase())} onChange={e => set(f.name, e.target.checked ? '1' : '0')} className="h-4 w-4" />
              {f.label}
            </label>
          )
          const secret = meta?.kind === 'secret'
          return (
            <div key={f.name}>
              <label htmlFor={id} className="mb-1 flex items-center gap-2 text-sm font-semibold">
                {f.label}
                {meta?.source === 'env' && <span className="rounded bg-surface-2 px-1.5 text-xs font-normal text-muted" title={t('Kommt aus der Datei .env. Speichern hier überschreibt ihn.')}>{t('aus .env')}</span>}
              </label>
              <input id={id} className={input} type={secret ? 'password' : 'text'} autoComplete="off" value={vals[f.name] ?? ''}
                placeholder={secret && meta?.set ? t('gesetzt, leer lassen zum Behalten') : ''} onChange={e => set(f.name, e.target.value)} />
              {paths[f.name] ? <p className={`mt-1 text-xs ${paths[f.name].ok ? 'text-muted' : 'text-bad'}`}>{paths[f.name].text}</p>
                : f.hint && <p className="mt-1 text-xs text-muted">{f.hint}</p>}
            </div>
          )
        })}
      </div>
      {result?.libraries && result.libraries.length > 0 && (
        <div className="mt-4 text-sm">
          <p className="mb-1 font-semibold">{t('Hörbuch-Bibliotheken in Audiobookshelf')}</p>
          <ul className="space-y-1">
            {result.libraries.map(l => (
              <li key={l.id} className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{l.name}</span><span className="text-xs text-muted">{l.folders.join(', ')}</span>
                <button type="button" className="rounded border border-line px-2 text-xs hover:bg-surface-2" onClick={() => set('ABS_LIBRARY_EN', l.id)}>{t('als Englisch')}</button>
                <button type="button" className="rounded border border-line px-2 text-xs hover:bg-surface-2" onClick={() => set('ABS_LIBRARY_DE', l.id)}>{t('als Deutsch')}</button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {result?.indexers && result.indexers.length > 0 && (
        <div className="mt-4 overflow-x-auto text-sm">
          <table className="w-full text-left">
            <thead className="text-xs text-faint"><tr><th className="py-1 pr-3">{t('Indexer')}</th><th className="py-1 pr-3">{t('Art')}</th><th className="py-1 pr-3">{t('Hörbücher')}</th><th className="py-1">{t('E-Books (Torrent)')}</th></tr></thead>
            <tbody>
              {result.indexers.map(ix => {
                const key = ix.protocol === 'torrent' ? 'TORRENT_INDEXER_IDS' : 'USENET_INDEXER_IDS'
                return (
                  <tr key={ix.id} className="border-t border-line">
                    <td className="py-1.5 pr-3">{ix.name}{!ix.enabled && <span className="text-xs text-muted"> {t('(aus)')}</span>}</td>
                    <td className="py-1.5 pr-3 text-muted">{ix.protocol === 'torrent' ? t('Torrent') : t('Usenet')}</td>
                    <td className="py-1.5 pr-3"><input type="checkbox" aria-label={t('{{v0}} für Hörbücher', { v0: ix.name })} checked={ids(key).has(String(ix.id))} onChange={() => toggle(key, ix.id)} className="h-4 w-4" /></td>
                    <td className="py-1.5">{ix.protocol === 'torrent' && <input type="checkbox" aria-label={t('{{v0}} für E-Books', { v0: ix.name })} checked={ids('EBOOK_TORRENT_INDEXER_IDS').has(String(ix.id))} onChange={() => toggle('EBOOK_TORRENT_INDEXER_IDS', ix.id)} className="h-4 w-4" />}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        {group.test && <Button onClick={test} disabled={busy !== null}>{busy === 'test' ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <PlugZap size={15} aria-hidden />}{t('Verbindung testen')}</Button>}
        <Button variant="primary" onClick={save} disabled={busy !== null}><Save size={15} aria-hidden />{saveLabel}</Button>
        {result && <span role="status" className={`inline-flex items-center gap-1.5 text-sm ${result.ok ? '' : 'text-bad'}`}>{result.ok ? <CheckCircle2 size={15} style={{ color: 'var(--ok)' }} aria-hidden /> : <CircleAlert size={15} aria-hidden />}{result.text}</span>}
        {msg && <span role="status" className={`text-sm ${msg.ok ? 'text-muted' : 'text-bad'}`}>{msg.text}</span>}
      </div>
    </div>
  )
}
