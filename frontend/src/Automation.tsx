import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, useData } from './api'
import { locale, t } from './i18n'

interface Unsure { book_id: number; title: string; sequence: string | null; lang: string; series: string; series_id: number; unsure: number; last_search: string }
interface Damaged { book_id: number; title: string; series: string | null; series_id: number | null; detail: string; download_state: string | null }
interface Integrity { total: number; checked: number; ok: number; errors: number; damaged: Damaged[] }
interface Auto { auto_integrity: boolean; integrity: Integrity; auto_ebook: boolean; auto_ebook_upcoming: boolean; auto_ebook_listening: boolean; notify_ebook: boolean; auto_author_series: boolean; auto_convert: boolean; auto_chapters: boolean; notify: boolean; notify_configured: boolean; auto_cleanup: boolean; auto_search: boolean; auto_import: boolean; batch: number; open: number; due: number; never: number; unsure: Unsure[]; import_enabled: boolean }

export function Switch({ on, onChange, label, hint, disabled }: { on: boolean; onChange: (v: boolean) => void; label: string; hint: string; disabled?: boolean }) {
  return (
    <label className={`flex min-h-11 cursor-pointer items-start gap-3 ${disabled ? 'opacity-50' : ''}`}>
      <button type="button" role="switch" aria-checked={on} disabled={disabled} onClick={() => onChange(!on)}
        className={`relative mt-0.5 h-6 w-11 shrink-0 rounded-full transition-colors duration-150 ${on ? 'bg-accent-strong' : 'bg-surface-2 ring-1 ring-line'}`}>
        <span className={`absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform duration-150 ${on ? 'translate-x-5' : ''}`} />
        <span className="sr-only">{label}</span>
      </button>
      <span><span className="block text-sm font-semibold">{label} <span className="font-normal text-muted">({on ? t('ein') : t('aus')})</span></span><span className="block text-sm text-muted">{hint}</span></span>
    </label>
  )
}

function damagedState(s: string | null) {
  if (s === 'queued' || s === 'downloading') return t('Ersatz lädt.')
  if (s === 'arrived' || s === 'verified') return t('Ersatz wird geprüft.')
  if (s === 'review') return t('Ersatz wartet im Tab «Prüfen».')
  return t('Wird gesucht.')
}

/** Hauptschalter der Automatik und Liste der Bände, bei denen nur unsichere Treffer vorliegen. */
export function AutomationPanel() {
  const { data, reload } = useData<Auto>('/automation', 10000)
  const [err, setErr] = useState<string | null>(null)
  if (!data) return <div className="skeleton h-40 rounded-xl" />
  const set = async (body: Partial<Pick<Auto, 'auto_integrity' | 'auto_search' | 'auto_import' | 'auto_cleanup' | 'auto_convert' | 'auto_chapters' | 'notify' | 'auto_ebook' | 'auto_ebook_upcoming' | 'auto_ebook_listening' | 'notify_ebook' | 'auto_author_series'>>) => {
    setErr(null)
    try { await api('/automation', { method: 'POST', body: JSON.stringify(body) }); reload() } catch (e) { setErr((e as Error).message) }
  }
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Switch on={data.auto_search} onChange={v => set({ auto_search: v })} label={t('Automatische Suche')}
          hint={t('Sucht alle 15 Minuten {{v0}} fällige Bände und lädt sichere Treffer sofort. Nach erfolgloser Suche wartet ein Band 1, 2, 4, dann 7 Tage.', { v0: data.batch })} />
        <Switch on={data.auto_import} onChange={v => set({ auto_import: v })} disabled={!data.import_enabled} label={t('Bestandene Bücher automatisch ablegen')}
          hint={t('Gilt für jeden Download mit bestandener Prüfung, auch von Hand gestartete. Eindeutig bessere Fassungen ersetzen den Bestand (alt in den Papierkorb), ausser du hörst das Buch gerade. Hinweise und belegte Zielordner warten im Tab «Prüfen».')} />
        <Switch on={data.auto_cleanup} onChange={v => set({ auto_cleanup: v })} label={t('Automatisch aufräumen')}
          hint={t('Verschiebt Download-Reste einen Tag nach Abschluss in den Papierkorb. Endgültig gelöscht wird nur von Hand.')} />
        <Switch on={data.auto_convert} onChange={v => set({ auto_convert: v })} label={t('MP3 zu M4B umwandeln')}
          hint={t('Wandelt jede Stunde ein Buch aus vielen Dateien in eine M4B mit Kapiteln um, kleinste zuerst. Die Originale gehen in den Papierkorb.')} />
        <Switch on={data.auto_chapters} onChange={v => set({ auto_chapters: v })} label={t('Kapitel ergänzen')}
          hint={t('Setzt bei Büchern aus einer Datei ohne Kapitel die Kapitel von Audible in Audiobookshelf, wenn die Laufzeit passt. Die Datei bleibt unverändert.')} />
        <Switch on={data.auto_ebook} onChange={v => set({ auto_ebook: v })} label={t('E-Books zu neuen Hörbüchern laden')}
          hint={t('Sucht alle 30 Minuten zu drei von ABW abgelegten Hörbüchern das EPUB (Usenet, sonst EBookBay und Internet Archive). Jedes EPUB wird nach dem Download geprüft und kommt als neue Datei in den Buchordner.')} />
        <Switch on={data.auto_ebook_listening} onChange={v => set({ auto_ebook_listening: v })} label={t('E-Books zu Serien laden, die gerade gehört werden')}
          hint={t('Fängt ein Benutzer ein Buch an, sucht ABW zuerst die EPUBs aller vorhandenen Bände dieser Serie (der gehörte Band und die folgenden zuerst), mit eingeschalteter Ablage auch die angekündigten. Gilt 14 Tage nach dem letzten Hören, drei Bände je Lauf vor den übrigen E-Book-Suchen.')} />
        <Switch on={data.auto_ebook_upcoming} onChange={v => set({ auto_ebook_upcoming: v })} disabled={!data.import_enabled} label={t('E-Books zu angekündigten Bänden vorab laden')}
          hint={t('Ist das Hörbuch eines Bandes noch nicht erschienen, sucht ABW schon das EPUB und legt es in den künftigen Buchordner. Das Hörbuch kommt später in denselben Ordner. Bände mit Termin zuerst.')} />
        <Switch on={data.notify_ebook} onChange={v => set({ notify_ebook: v })} label={t('Meldungen zu abgelegten E-Books')}
          hint={t('Meldet in der Glocke und per Push, wenn ABW ein EPUB in einen Buchordner gelegt hat. Ausgeschaltet laden die E-Books still weiter.')} />
        <Switch on={data.auto_author_series} onChange={v => set({ auto_author_series: v })} label={t('Serien gehörter Autoren aufnehmen')}
          hint={t('Hat irgendein Benutzer ein Buch fertig gehört, kommen alle Serien dieses Autors auf Deutsch und Englisch in die Bibliothek und werden gesucht, auch rückwirkend. Ohne gekürzte Fassungen. Entfernte Serien bleiben weg.')} />
        <Switch on={data.auto_integrity} onChange={v => set({ auto_integrity: v })} label={t('Bestand auf Schäden prüfen')}
          hint={t('Liest nachts (01–07 Uhr) jede Hörbuchdatei einmal ganz und sucht Lücken aus Null-Bytes, wie sie unfertige Torrents hinterlassen. Beschädigte Bücher werden neu gesucht und nach bestandener Prüfung ersetzt. Geprüft: {{v0}} von {{v1}} Büchern.', { v0: data.integrity.checked.toLocaleString(locale()), v1: data.integrity.total.toLocaleString(locale()) })} />
        <div>
          <Switch on={data.notify} onChange={v => set({ notify: v })} disabled={!data.notify_configured} label={t('Meldungen über ntfy')}
            hint={data.notify_configured ? t('Meldet neue Bücher im Bestand, angekündigte Bände und Fälle für das Tab «Prüfen».') : t('ntfy ist noch nicht eingerichtet (Adresse oder Thema fehlt).')} />
          {data.notify_configured && (
            <button type="button" className="ml-14 mt-1 min-h-9 text-sm font-medium text-accent underline-offset-4 hover:underline"
              onClick={async () => { setErr(null); try { await api('/notify/test', { method: 'POST' }); setErr(t('Testmeldung gesendet.')) } catch (e) { setErr((e as Error).message) } }}>
              {t('Testmeldung senden')}
            </button>
          )}
        </div>
      </div>
      {err && <p role="status" className="mt-3 text-sm">{err}</p>}
      <dl className="mt-4 grid grid-cols-3 gap-3 border-t border-line pt-4 text-sm">
        <div><dt className="text-faint">{t('Fehlende Bände im Suchbereich')}</dt><dd className="tabular text-xl font-bold">{data.open.toLocaleString(locale())}</dd></div>
        <div><dt className="text-faint">{t('Jetzt fällig')}</dt><dd className="tabular text-xl font-bold">{data.due.toLocaleString(locale())}</dd></div>
        <div><dt className="text-faint">{t('Noch nie gesucht')}</dt><dd className="tabular text-xl font-bold">{data.never.toLocaleString(locale())}</dd></div>
      </dl>
      {data.integrity.damaged.length > 0 && (
        <div className="mt-4 border-t border-line pt-3 text-sm">
          <p className="font-semibold">{data.integrity.damaged.length} {data.integrity.damaged.length === 1 ? t('Buch') : t('Bücher')} {t('im Bestand beschädigt')}</p>
          <ul className="mt-1 space-y-1">
            {data.integrity.damaged.map(d => (
              <li key={d.book_id}>
                {d.series_id ? <Link to={`/serie/${d.series_id}`} className="font-medium text-accent underline-offset-4 hover:underline">{d.title}</Link> : <span className="font-medium">{d.title}</span>}
                <span className="text-muted">: {d.detail}. {damagedState(d.download_state)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {data.unsure.length > 0 && (
        <p className="mt-4 border-t border-line pt-3 text-sm">
          <Link to="/pruefen" className="font-semibold text-accent underline-offset-4 hover:underline">{data.unsure.length} {t('Bände mit unsicheren Treffern')}</Link>
          <span className="text-muted"> {t('warten im Tab «Prüfen» auf deine Auswahl.')}</span>
        </p>
      )}
    </div>
  )
}
