import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Combine, Unlink } from 'lucide-react'
import { api, useData } from './api'
import { Button, ErrorBox, ask } from './ui'
import { t } from './i18n'
import type { AuthorTile } from './pages/AuthorsPage'

interface Pair { a: { key: string; name: string }; b: { key: string; name: string } }

/** Vorschläge für doppelt geführte Autoren (gleicher Nachname, Vornamen als Initialen passend), nur für Administratoren. */
export function DuplicateAuthors({ onChange }: { onChange: () => void }) {
  const { data, reload } = useData<Pair[]>('/authors/duplicates')
  const [err, setErr] = useState<string | null>(null)
  if (!data?.length) return null
  const merge = async (from: { key: string; name: string }, into: { key: string; name: string }) => {
    if (!await ask(t('«{{v0}}» künftig als «{{v1}}» führen? Serien, Markierungen und Katalog laufen dann unter diesem Namen.', { v0: from.name, v1: into.name }), t('Zusammenführen'))) return
    setErr(null)
    try { await api(`/authors/${from.key}/merge`, { method: 'POST', body: JSON.stringify({ into: into.key }) }); reload(); onChange() } catch (e) { setErr((e as Error).message) }
  }
  return (
    <section className="mb-6 rounded-xl border border-line bg-surface p-4">
      <h2 className="font-semibold">{t('Vielleicht derselbe Autor')}</h2>
      <p className="mb-3 text-sm text-muted">{t('Audible führt manche Autoren unter mehreren Schreibweisen. Zusammengeführt zählen Serien und Markierungen für einen Namen; Trennen geht auf der Autorenseite.')}</p>
      {err && <ErrorBox message={err} plain />}
      <ul className="divide-y divide-line">
        {data.slice(0, 8).map(p => (
          <li key={p.a.key + p.b.key} className="flex flex-wrap items-center gap-2 py-2 text-sm">
            <span className="min-w-0 flex-1"><Link to={`/autor/${p.a.key}`} className="font-medium hover:underline">{p.a.name}</Link> · <Link to={`/autor/${p.b.key}`} className="font-medium hover:underline">{p.b.name}</Link></span>
            <Button onClick={() => merge(p.b, p.a)}><Combine size={15} aria-hidden />{t('Als «{{v0}}» führen', { v0: p.a.name })}</Button>
            <Button onClick={() => merge(p.a, p.b)}><Combine size={15} aria-hidden />{t('Als «{{v0}}» führen', { v0: p.b.name })}</Button>
          </li>
        ))}
      </ul>
    </section>
  )
}

/** Auf der Autorenseite: weitere Schreibweisen zeigen und trennen, einen anderen Autor hierher zusammenführen. */
export function AuthorAliases({ authorKey, name, aliases, onChange }: { authorKey: string; name: string; aliases: { key: string; name: string }[]; onChange: () => void }) {
  const { data: all } = useData<AuthorTile[]>('/authors')
  const [pick, setPick] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const others = (all ?? []).filter(a => a.key !== authorKey)
  const chosen = others.find(a => a.name === pick)
  const merge = async () => {
    if (!chosen || !await ask(t('«{{v0}}» künftig als «{{v1}}» führen? Serien, Markierungen und Katalog laufen dann unter diesem Namen.', { v0: chosen.name, v1: name }), t('Zusammenführen'))) return
    setErr(null)
    try { await api(`/authors/${chosen.key}/merge`, { method: 'POST', body: JSON.stringify({ into: authorKey }) }); setPick(''); onChange() } catch (e) { setErr((e as Error).message) }
  }
  const split = async (a: { key: string; name: string }) => {
    setErr(null)
    try { await api(`/authors/alias/${a.key}`, { method: 'DELETE' }); onChange() } catch (e) { setErr((e as Error).message) }
  }
  return (
    <div className="space-y-3">
      {err && <ErrorBox message={err} plain />}
      {aliases.length > 0 && (
        <ul className="flex flex-wrap gap-2">
          {aliases.map(a => (
            <li key={a.key} className="inline-flex items-center gap-2 rounded-lg border border-line bg-surface py-1 pl-3 pr-1 text-sm">
              {a.name}
              <Button onClick={() => split(a)} title={t('Wieder als eigenen Autor führen')}><Unlink size={14} aria-hidden />{t('Trennen')}</Button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap gap-2">
        <input list="abw-authors" value={pick} onChange={e => setPick(e.target.value)} placeholder={t('Anderen Autor wählen')} aria-label={t('Autor, der hierher gehört')}
          className="h-10 min-w-0 flex-1 rounded-lg border border-line bg-bg px-3 text-sm sm:max-w-sm" />
        <datalist id="abw-authors">{others.map(a => <option key={a.key} value={a.name} />)}</datalist>
        <Button onClick={merge} disabled={!chosen}><Combine size={15} aria-hidden />{t('Hierher zusammenführen')}</Button>
      </div>
    </div>
  )
}
