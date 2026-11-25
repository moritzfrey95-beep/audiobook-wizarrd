import { AlertTriangle, CheckCircle2, Clock, Loader2, Play } from 'lucide-react'
import { api, fmtDateTime, useData } from '../api'
import { Button, ErrorBox, Page, Section } from '../ui'
import { AutomationPanel } from '../Automation'
import { CleanupPanel } from '../Cleanup'
import { ConvertPanel } from '../Convert'
import { UpgradePanel } from '../Upgrade'
import { t } from '../i18n'

interface Run { id: number; key: string; name: string; started: string; finished: string | null; status: string; message: string | null }
interface Task { key: string; name: string; description: string; interval_min: number; running: boolean; queued: boolean; progress: string; last: Run | null; next: string | null }
interface Overview { tasks: Task[]; runs: Run[]; planned: { key: string; name: string; description: string; stage: string }[] }

const every = (min: number) => min < 60 ? t('alle {{v0}} Minuten', { v0: min }) : min < 1440 ? t('alle {{v0}} Stunden', { v0: min / 60 }) : t('täglich')

function RunStatus({ status }: { status: string }) {
  if (status === 'running') return <span className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: 'var(--load)' }}><Loader2 size={14} className="animate-spin" aria-hidden />{t('Läuft')}</span>
  if (status === 'ok') return <span className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: 'var(--ok)' }}><CheckCircle2 size={14} aria-hidden />{t('Erfolgreich')}</span>
  return <span className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: 'var(--bad)' }}><AlertTriangle size={14} aria-hidden />{t('Fehler')}</span>
}

export default function TasksPage() {
  const { data, error, reload } = useData<Overview>('/tasks', 3000)
  if (error && !data) return <Page title={t('Aufgaben')}><ErrorBox message={error} onRetry={reload} /></Page>
  if (!data) return <Page title={t('Aufgaben')}><div className="skeleton h-64 rounded-2xl" /></Page>
  const start = async (key: string) => { await api(`/tasks/${key}/run`, { method: 'POST' }); reload() }
  return (
    <Page title={t('Aufgaben')} subtitle={t('Zyklische Aufgaben laufen selbständig. Jede lässt sich auch sofort starten.')}>
      <Section title={t('Automatik')}><AutomationPanel /></Section>
      <Section title={t('Qualitäts-Upgrade')}><UpgradePanel /></Section>
      <Section title={t('Zyklische Aufgaben')}>
        <ul className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {data.tasks.map(tx => (
            <li key={tx.key} className="flex flex-col gap-3 rounded-xl border border-line bg-surface p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="text-base font-semibold">{tx.name}</h3>
                  <p className="mt-0.5 text-sm text-muted">{tx.description}</p>
                </div>
                <Button onClick={() => start(tx.key)} disabled={tx.running || tx.queued}>
                  <Play size={14} aria-hidden />{tx.running ? t('Läuft') : tx.queued ? t('Eingereiht') : t('Jetzt starten')}
                </Button>
              </div>
              <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm text-muted">
                <span className="inline-flex items-center gap-1.5"><Clock size={14} aria-hidden />{every(tx.interval_min)}</span>
                {tx.running ? <RunStatus status="running" /> : tx.last ? <RunStatus status={tx.last.status} /> : <span>{t('Noch nie gelaufen')}</span>}
                {tx.last && <span className="tabular">{t('Letzter Lauf')}{' '}{fmtDateTime(tx.last.started)}</span>}
                {tx.next && !tx.running && <span className="tabular">{t('Nächster Lauf')}{' '}{fmtDateTime(tx.next)}</span>}
              </div>
              {(tx.running ? tx.progress : tx.last?.message) && (
                <p className="rounded-lg bg-surface-2 px-3 py-2 text-sm" aria-live="polite">{tx.running ? tx.progress : tx.last?.message}</p>
              )}
            </li>
          ))}
        </ul>
      </Section>
      <Section title={t('Umwandlung und Kapitel')}><ConvertPanel /></Section>
      <Section title={t('Aufräumen')} hint={t('Download-Reste von Usenet und AudioBookBay')}><CleanupPanel /></Section>
      {data.planned.length > 0 && <Section title={t('Geplante Aufgaben')} hint={t('Noch nicht gebaut')}>
        <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {data.planned.map(p => (
            <li key={p.key} className="rounded-xl border border-dashed border-line p-4">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-base font-semibold text-muted">{p.name}</h3>
                <span className="rounded-full bg-surface-2 px-2.5 py-1 text-xs font-semibold text-muted">{p.stage}</span>
              </div>
              <p className="mt-1 text-sm text-faint">{p.description}</p>
            </li>
          ))}
        </ul>
      </Section>}
      <Section title={t('Verlauf')} hint={t('Routineläufe ohne Fehler erscheinen nur einmal')}>
        <div className="overflow-x-auto rounded-xl border border-line bg-surface">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-faint">
              <tr><th className="px-4 py-3 font-semibold">{t('Aufgabe')}</th><th className="px-4 py-3 font-semibold">{t('Start')}</th><th className="px-4 py-3 font-semibold">{t('Status')}</th><th className="px-4 py-3 font-semibold">{t('Ergebnis')}</th></tr>
            </thead>
            <tbody>
              {data.runs.map(r => (
                <tr key={r.id} className="border-t border-line">
                  <td className="px-4 py-2.5 font-medium">{r.name}</td>
                  <td className="tabular px-4 py-2.5 text-muted">{fmtDateTime(r.started)}</td>
                  <td className="px-4 py-2.5"><RunStatus status={r.status} /></td>
                  <td className="px-4 py-2.5 text-muted">{r.message ?? '–'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </Page>
  )
}
