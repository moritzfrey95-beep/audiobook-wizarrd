import { useState } from 'react'
import { Star } from 'lucide-react'
import { locale, t } from './i18n'

/** Fünf Sterne zum Antippen (Bewertungen). «small» für die Bandkarte, sonst 44 px hohe Ziele. */
export function Stars({ value, onPick, label, small }: { value: number | null | undefined; onPick: (n: number) => void; label: string; small?: boolean }) {
  const [hover, setHover] = useState(0)
  const shown = hover || value || 0
  return (
    <div className="flex" role="radiogroup" aria-label={label} onMouseLeave={() => setHover(0)}>
      {[1, 2, 3, 4, 5].map(n => (
        <button key={n} type="button" role="radio" aria-checked={value === n} aria-label={`${n} ${n === 1 ? t('Stern') : t('Sterne')}`}
          onClick={() => onPick(n)} onMouseEnter={() => setHover(n)}
          className={`grid place-items-center rounded-md transition-transform duration-100 active:scale-90 ${small ? 'h-9 w-7' : 'h-11 w-9'}`}>
          <Star size={small ? 17 : 22} aria-hidden fill={n <= shown ? 'var(--warn)' : 'none'} style={{ color: n <= shown ? 'var(--warn)' : 'var(--faint)' }} />
        </button>
      ))}
    </div>
  )
}

/** Feste Sterne zum Anzeigen, z. B. «★★★★☆». */
export function StarText({ n }: { n: number }) {
  const full = Math.round(n)
  return <span aria-label={t('{{v0}} von 5 Sternen', { v0: n.toLocaleString(locale()) })} style={{ color: 'var(--warn)' }}>{'★'.repeat(full)}<span className="text-faint">{'★'.repeat(5 - full)}</span></span>
}
