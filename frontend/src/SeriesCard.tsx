import { Link } from 'react-router-dom'
import { Headphones } from 'lucide-react'
import type { Series, SingleGroup } from './api'
import { fmtDate } from './api'
import { Badge, Cover, EditionRow, LangTag, shownFlags } from './ui'
import { StarText } from './Stars'
import { locale, t } from './i18n'

export function SeriesCard({ s }: { s: Series }) {
  const [primary, ...rest] = shownFlags(s.flags)
  const extra = rest.filter(f => f !== 'unmonitored' && f !== 'unknown' && f !== 'single' && f !== 'absent')
  return (
    <Link to={`/serie/${s.id}`}
      className="group flex flex-col overflow-hidden rounded-xl border border-line bg-surface transition duration-200 hover:-translate-y-0.5 hover:border-faint hover:shadow-[var(--shadow)]">
      <div className="relative">
        <Cover src={s.cover} alt={s.name} />
        {primary && <div className="absolute left-2 top-2"><Badge status={primary} solid /></div>}
        {s.next_release && (
          <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/85 to-transparent px-2.5 pb-2 pt-6 text-xs font-medium text-white">
            {t('Nächster Band:')}{' '}{fmtDate(s.next_release)}
          </div>
        )}
      </div>
      <div className="flex flex-1 flex-col gap-2 p-3">
        <div>
          <h3 className="line-clamp-2 text-[15px] font-semibold leading-snug group-hover:text-accent">{s.name}</h3>
          <p className="truncate text-xs text-muted">{s.author}</p>
          {s.listened > 0 && (
            <p className="mt-0.5 flex items-center gap-1 text-xs text-muted"><Headphones size={12} aria-hidden />{s.listened} {t('von')}{' '}{s.owned} {t('gehört')}</p>
          )}
          {s.rating && (
            <p className="mt-0.5 flex items-center gap-1 text-xs text-muted"
              title={t('Schnitt aller {{v0}} Bewertungen{{v1}}', { v0: s.rating.count, v1: s.rating.mine != null ? `, deine: ${s.rating.mine.toLocaleString(locale())}` : '' })}>
              <StarText n={s.rating.avg} /><span className="tabular">{s.rating.avg.toLocaleString(locale())}</span>
              <span className="text-faint">({s.rating.count})</span>
            </p>
          )}
        </div>
        <div className="mt-auto space-y-1.5">
          {s.editions.map(e => <EditionRow key={e.id} e={e} />)}
        </div>
        {extra.length > 0 && (
          <div className="flex flex-wrap gap-1">{extra.map(f => <Badge key={f} status={f} />)}</div>
        )}
      </div>
    </Link>
  )
}

/** Karte «Autor – Einzelbücher»: sieht aus wie eine Serienkarte, je Sprache «im Bestand / alle». */
export function SingleCard({ g }: { g: SingleGroup }) {
  const [primary, ...rest] = shownFlags(g.flags)
  const extra = rest.filter(f => f !== 'single')
  return (
    <Link to={`/einzelbuecher/${g.key}`}
      className="group flex flex-col overflow-hidden rounded-xl border border-line bg-surface transition duration-200 hover:-translate-y-0.5 hover:border-faint hover:shadow-[var(--shadow)]">
      <div className="relative">
        <Cover src={g.cover} alt={g.name} />
        {primary && <div className="absolute left-2 top-2"><Badge status={primary} solid /></div>}
        {g.next_release && (
          <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/85 to-transparent px-2.5 pb-2 pt-6 text-xs font-medium text-white">
            {t('Nächstes Buch:')}{' '}{fmtDate(g.next_release)}
          </div>
        )}
      </div>
      <div className="flex flex-1 flex-col gap-2 p-3">
        <div>
          <h3 className="line-clamp-2 text-[15px] font-semibold leading-snug group-hover:text-accent">{g.name}</h3>
          <p className="truncate text-xs text-muted">{g.total} {g.total === 1 ? t('Buch') : t('Bücher')} {t('ohne Serie')}</p>
          {g.listened > 0 && (
            <p className="mt-0.5 flex items-center gap-1 text-xs text-muted"><Headphones size={12} aria-hidden />{g.listened} {t('von')}{' '}{g.owned} {t('gehört')}</p>
          )}
          {g.rating && (
            <p className="mt-0.5 flex items-center gap-1 text-xs text-muted"
              title={t('Schnitt aller {{v0}} Bewertungen{{v1}}', { v0: g.rating.count, v1: g.rating.mine != null ? `, deine: ${g.rating.mine.toLocaleString(locale())}` : '' })}>
              <StarText n={g.rating.avg} /><span className="tabular">{g.rating.avg.toLocaleString(locale())}</span>
              <span className="text-faint">({g.rating.count})</span>
            </p>
          )}
        </div>
        <div className="mt-auto space-y-1.5">
          {g.langs.map(l => (
            <div key={l.lang} className="flex items-center gap-2 text-xs">
              <LangTag lang={l.lang} />
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-2">
                <div className="h-full rounded-full" style={{ width: `${Math.round((l.owned / l.total) * 100)}%`, background: 'var(--faint)' }} />
              </div>
              <span className="tabular w-14 text-right font-medium text-muted">{l.owned}/{l.total}</span>
            </div>
          ))}
        </div>
        {extra.length > 0 && (
          <div className="flex flex-wrap gap-1">{extra.map(f => <Badge key={f} status={f} />)}</div>
        )}
      </div>
    </Link>
  )
}
