import { useCallback, useEffect, useRef, useState } from 'react'
import { locale, t, uiLang } from './i18n'

export type Lang = 'en' | 'de'
export type Flag = 'downloading' | 'review' | 'failed' | 'searching' | 'incomplete' | 'upcoming' | 'complete' | 'unknown' | 'single' | 'absent' | 'unmonitored'
export type BookState = 'owned' | 'upcoming' | 'missing' | 'downloading' | 'searching' | 'review' | 'failed'

export interface Book {
  id: number; asin: string | null; title: string; full_title: string; also: { title: string; owned: boolean; abs_item_id: string | null }[]
  subtitle: string | null; author: string | null; narrator: string | null
  sequence: string | null; release_date: string | null; runtime_min: number | null; cover: string | null; state: BookState
  abs_item_id: string | null; size: number | null; num_files: number | null; progress: number | null; finished: boolean; epub: boolean
  ebook: { state: 'loading' | 'none' | 'error' | null; message: string | null } | null
  download: { state: string; progress: number | null; release_title: string; source: string } | null; status_from: 'abw' | null
  rating: { mine: number | null; skipped: boolean; others: { name: string; stars: number }[] } | null
}
export interface Edition {
  id: number; lang: Lang; name: string; audible_asin: string | null; monitored: boolean; link_source: string
  synced_at: string | null; sync_error: string | null; owned: number; total: number; missing: number; upcoming: number
  next_release: string | null; flags: Flag[]; books?: Book[]
  /** Fassung innerhalb der Sprache: Sprecher oder «BBC-Hörspiel»; extra = zusätzliche Fassung neben der Hauptfassung */
  variant: string | null; extra: boolean; drama: boolean
}
export interface Series {
  id: number; name: string; author: string; author_key: string; cover: string | null; flags: Flag[]; primary: Flag; added_at: string
  next_release: string | null; missing: number; hours: number; hours_editions?: number; listened: number; owned: number; auto: boolean; editions: Edition[]
  rating: { avg: number; count: number; mine: number | null } | null
  /** Sortierschlüssel wie im Backend (ohne Artikel), damit Einzelbuch-Karten richtig einsortiert werden */
  sort?: string
}
/** Karte «Autor – Einzelbücher» in der Serienansicht: alle Einzelbücher ohne Serie eines Autors. */
export interface SingleGroup {
  key: string; name: string; sort: string; author: string; author_key: string; cover: string | null; flags: Flag[]; primary: Flag
  added_at: string; next_release: string | null; missing: number; hours: number; listened: number; owned: number; total: number
  langs: { lang: Lang; owned: number; total: number; upcoming: number }[]
  titles: string[]
  rating: { avg: number; count: number; mine: number | null } | null
}
export interface SingleDetail extends SingleGroup {
  books: (Book & { lang: Lang; added_by: string | null; added_at: string | null })[]; abs_url: string; can_mark?: CanMark
}
/** Genre und Tags je Karte der Serienansicht (Schlüssel: Serien-ID bzw. Schlüssel der Einzelbuch-Karte). */
export interface LibraryTags {
  series: Record<number, { genres: string[]; tags: string[] }>
  singles: Record<string, { genres: string[]; tags: string[] }>
}
export interface Candidate { series_asin: string; series_title: string; book_count: number; cover_url: string | null; likely?: boolean }
export interface CanMark { can: boolean; why: string | null }
export interface SeriesDetail extends Series { candidates: Partial<Record<Lang, Candidate[]>>; abs_url: string; can_mark?: CanMark }

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch('/api' + path, { headers: { 'Content-Type': 'application/json', 'X-ABW-Lang': uiLang() }, ...init })
  if (r.status === 401 && !path.startsWith('/auth/')) window.dispatchEvent(new Event('abw-unauthorized'))
  if (!r.ok) {
    let detail = ''
    try { detail = (await r.json()).detail } catch { /* keine Fehlermeldung vom Server */ }
    throw new Error(detail || t('Der Server antwortet mit Fehler {{v0}}.', { v0: r.status }))
  }
  return r.json()
}

/** Lädt Daten, optional mit Auffrischen im Intervall. */
export function useData<T>(path: string | null, refreshMs = 0) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const current = useRef(path)
  const load = useCallback(async (quiet = false) => {
    if (!path) return
    if (!quiet) setLoading(true)
    try {
      const d = await api<T>(path)
      if (current.current === path) { setData(d); setError(null) }
    } catch (e) {
      if (current.current === path) setError((e as Error).message)
    } finally { setLoading(false) }
  }, [path])
  useEffect(() => { current.current = path; setData(null); load() }, [path, load])
  useEffect(() => {
    if (!refreshMs) return
    const tx = setInterval(() => { if (!document.hidden) load(true) }, refreshMs)
    return () => clearInterval(tx)
  }, [refreshMs, load])
  return { data, error, loading, reload: () => load(true) }
}

export const fmtDate = (d: string | null | undefined) =>
  d ? new Date(d).toLocaleDateString(locale(), { day: '2-digit', month: '2-digit', year: 'numeric' }) : t('Termin offen')
export const fmtDateTime = (d: string | null | undefined) =>
  d ? new Date(d).toLocaleString(locale(), { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '–'
export const fmtRuntime = (min: number | null | undefined) =>
  min ? `${Math.floor(min / 60)} h ${String(min % 60).padStart(2, '0')} min` : '–'
/** Umfang einer Serie: «12 Bände · 140 h». */
export const fmtSeriesSize = (books: number | null | undefined, min: number | null | undefined) =>
  [books ? `${books} ${books === 1 ? t('Band') : t('Bände')}` : null, min ? `${Math.max(1, Math.round(min / 60))} h` : null].filter(Boolean).join(' · ')
export const fmtNum = (n: number) => n.toLocaleString(locale())
