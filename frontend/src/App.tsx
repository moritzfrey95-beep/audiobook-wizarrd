import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import {
  BarChart3, Bell, BookmarkCheck, CalendarDays, ClipboardCheck, Compass, Headphones, History, LibraryBig, ListChecks, LogOut, Moon,
  PenLine, Send, Settings, Sparkles, Star, Sun, Tags, Trophy, UserRound, Users, WandSparkles, Wrench, type LucideIcon, Languages } from 'lucide-react'
import { api, useData } from './api'
import ReviewPage from './pages/ReviewPage'
import SeriesPage from './pages/SeriesPage'
import SeriesDetailPage from './pages/SeriesDetailPage'
import SinglesDetailPage from './pages/SinglesDetailPage'
import AuthorsPage from './pages/AuthorsPage'
import AuthorDetailPage from './pages/AuthorDetailPage'
import DiscoverPage from './pages/DiscoverPage'
import CalendarPage from './pages/CalendarPage'
import TasksPage from './pages/TasksPage'
import StatsPage from './pages/StatsPage'
import ListeningPage from './pages/ListeningPage'
import SettingsPage from './pages/SettingsPage'
import LoginPage from './pages/LoginPage'
import NotificationsPage from './pages/NotificationsPage'
import RatingsPage from './pages/RatingsPage'
import ToReadPage from './pages/ToReadPage'
import { NotificationBell } from './Notifications'
import { SetupWizard, type SetupState } from './SetupWizard'
import { MeContext, useAdmin, useMe, type Me } from './session'
import { Count, DialogHost, Empty, tabClass } from './ui'
import { setUiLang, t, uiLang } from './i18n'

type CountKey = 'review' | 'ratings' | 'notes'
type Counts = Record<CountKey, number> & { reviewUsers: number }
interface Item { to: string; label: string; icon: LucideIcon; count?: CountKey; also?: string[] }
interface Area { key: string; label: string; icon: LucideIcon; items: Item[]; admin?: boolean }

/** Bereiche nach Tätigkeit (Vorschlag 1 mit Seitenleiste). Jedes Register hat eine eigene Adresse. */
const AREAS: Area[] = [
  { key: 'hoeren', label: t('Hören'), icon: Headphones, items: [
    { to: '/to-read', label: t('To Read'), icon: BookmarkCheck },
    { to: '/bewertungen', label: t('Bewerten'), icon: Star, count: 'ratings' },
    { to: '/to-read/empfehlen', label: t('Weiterempfehlen'), icon: Send },
  ] },
  { key: 'bibliothek', label: t('Bibliothek'), icon: LibraryBig, items: [
    { to: '/', label: t('Serien'), icon: LibraryBig, also: ['/serie/'] },
    { to: '/autoren', label: t('Autoren'), icon: Users, also: ['/autor/'] },
  ] },
  { key: 'entdecken', label: t('Entdecken'), icon: Compass, items: [
    { to: '/entdecken', label: t('Für dich'), icon: Sparkles },
    { to: '/entdecken/autoren', label: t('Von deinen Autoren'), icon: PenLine },
    { to: '/entdecken/toplisten', label: t('Toplisten'), icon: Trophy },
  ] },
  { key: 'neu', label: t('Neu'), icon: Bell, items: [
    { to: '/benachrichtigungen', label: t('Meldungen'), icon: Bell, count: 'notes' },
    { to: '/kalender', label: t('Kalender'), icon: CalendarDays },
  ] },
  { key: 'rueckblick', label: t('Rückblick'), icon: BarChart3, items: [
    { to: '/hoerverlauf', label: t('Hörverlauf'), icon: History },
    { to: '/statistik', label: t('Statistik'), icon: BarChart3 },
    { to: '/bewertungen/meine', label: t('Meine Bewertungen'), icon: Star },
    { to: '/bewertungen/tags', label: t('Topliste je Tag'), icon: Tags },
  ] },
  { key: 'werkstatt', label: t('Werkstatt'), icon: Wrench, admin: true, items: [
    { to: '/pruefen', label: t('Prüfen'), icon: ClipboardCheck, count: 'review' },
    { to: '/aufgaben', label: t('Aufgaben'), icon: ListChecks },
  ] },
]

/** Untere Leiste (Handy/Tablet). Administratoren erreichen «Neu» über die Glocke. */
const BAR_ADMIN = ['hoeren', 'bibliothek', 'entdecken', 'rueckblick', 'werkstatt']
const BAR_USER = ['hoeren', 'bibliothek', 'entdecken', 'neu', 'rueckblick']

const COUNT_LABEL: Record<CountKey, string> = { review: t('offene Entscheidungen und Trefferwahlen'), ratings: t('unbewertete Bücher'), notes: t('ungelesene Meldungen') }

/** Zuletzt besuchtes Register je Bereich: ein Tipp auf den Bereich in der unteren Leiste führt dorthin zurück. */
const lastVisit: Record<string, string> = {}

const isOn = (it: Item, pathname: string) => pathname === it.to || !!it.also?.some(p => pathname.startsWith(p))
const areaOf = (areas: Area[], pathname: string) => areas.find(a => a.items.some(it => isOn(it, pathname)))

/** Fragt, ob eine Anmeldung nötig ist; zeigt bis dahin nichts und danach Anmeldung oder Anwendung. */
export default function App() {
  const [signedIn, setSignedIn] = useState<boolean | null>(null)
  const [blocked, setBlocked] = useState<string | null>(null)
  const [setup, setSetup] = useState<SetupState | null>(null)
  const [me, setMe] = useState<Me>({ id: 1, user: null, role: 'admin', langs: ['en', 'de'] })
  const check = () => fetch('/api/auth/state').then(async r => {
    if (r.status === 403) { setBlocked((await r.json()).detail ?? t('Kein Zugriff.')); return }
    const st = await r.json()
    if (st.authenticated && st.role !== 'user') fetch('/api/setup').then(r => r.ok ? r.json() : null).then(setSetup).catch(() => setSetup(null))
    if (st.authenticated) setMe({ id: st.id ?? 1, user: st.user ?? null, role: st.role === 'user' ? 'user' : 'admin', langs: st.langs?.length ? st.langs : ['en', 'de'] })
    setSignedIn(st.authenticated)
  }).catch(() => setSignedIn(true))
  useEffect(() => {
    check()
    const out = () => setSignedIn(false)
    window.addEventListener('abw-unauthorized', out)
    return () => window.removeEventListener('abw-unauthorized', out)
  }, [])
  if (blocked) return <main className="flex min-h-dvh items-center justify-center p-6"><p role="alert" className="max-w-md rounded-2xl border border-line bg-surface p-6 text-sm">{blocked}</p></main>
  if (signedIn === null) return <div className="min-h-dvh" aria-busy="true" />
  if (!signedIn) return <LoginPage onDone={check} />
  if (setup?.needed) return <MeContext.Provider value={me}><SetupWizard state={setup} onDone={() => { setSetup(null); check() }} /></MeContext.Provider>
  return <MeContext.Provider value={me}><Shell /></MeContext.Provider>
}

/** Prüfen zählt «Entscheiden» und «Treffer wählen» zusammen. */
function useCounts(admin: boolean): Counts {
  const rc = useData<{ open: number; users?: number }>(admin ? '/review/count' : null, 20000).data
  const review = rc?.open ?? 0
  const ratings = useData<{ open: number }>('/ratings/count', 30000).data?.open ?? 0
  const notes = useData<{ unread: number }>('/notifications?limit=1', 30000).data?.unread ?? 0
  return { review, ratings, notes, reviewUsers: rc?.users ?? 0 }
}

function Shell() {
  const admin = useAdmin()
  const { pathname } = useLocation()
  const counts = useCounts(admin)
  const areas = AREAS.filter(a => admin || !a.admin)
  const area = areaOf(areas, pathname)
  useEffect(() => { if (area) lastVisit[area.key] = pathname }, [area, pathname])
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme ?? 'dark')
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('abw-theme', theme) } catch { /* Einstellung gilt dann nur für diese Sitzung */ }
  }, [theme])
  // Sperrt auf Handy/Tablet das Scrollen der ganzen Seite (index.css), sonst schiebt z. B. Scrollen mit zwei Fingern Kopf und Leiste weg
  useEffect(() => {
    document.documentElement.classList.add('abw-app')
    return () => document.documentElement.classList.remove('abw-app')
  }, [])
  return (
    // Handy/Tablet: nur der Inhalt scrollt, Kopf und untere Leiste stehen fest (sonst wandern sie mit der Adressleiste des Browsers).
    <div className="fixed inset-0 flex flex-col lg:static lg:block lg:min-h-dvh">
      <header className="z-30 shrink-0 border-b border-line bg-bg/90 backdrop-blur lg:sticky lg:top-0">
        <div className="flex h-[60px] items-center gap-2 px-4 sm:px-6">
          <NavLink to="/" className="mr-2 flex shrink-0 items-center gap-2.5" aria-label={t('AudioBook Wizarrd, zur Serienübersicht')}>
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent-strong text-on-accent"><WandSparkles size={19} aria-hidden /></span>
            <span className="font-[family-name:var(--font-display)] text-lg font-bold tracking-tight">{t('AudioBook Wizarrd')}</span>
          </NavLink>
          <div className="flex-1" />
          <NotificationBell />
          <ProfileMenu theme={theme} setTheme={setTheme} />
        </div>
      </header>
      <DialogHost />
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <SideBar areas={areas} counts={counts} />
        {area && area.items.length > 1 && <AreaTabs area={area} counts={counts} />}
        <div id="abw-scroll" className="min-h-0 min-w-0 flex-1 overflow-y-auto overscroll-contain lg:overflow-visible">
          <Routes>
            <Route path="/" element={<SeriesPage />} />
            <Route path="/serie/:id" element={<SeriesDetailPage />} />
            <Route path="/einzelbuecher/:key" element={<SinglesDetailPage />} />
            <Route path="/autoren" element={<AuthorsPage />} />
            <Route path="/autor/:key" element={<AuthorDetailPage />} />
            <Route path="/entdecken" element={<DiscoverPage tab="foryou" />} />
            <Route path="/entdecken/autoren" element={<DiscoverPage tab="authors" />} />
            <Route path="/entdecken/toplisten" element={<DiscoverPage tab="top" />} />
            <Route path="/to-read" element={<ToReadPage tab="list" />} />
            <Route path="/to-read/empfehlen" element={<ToReadPage tab="recommend" />} />
            <Route path="/bewertungen" element={<RatingsPage tab="open" />} />
            <Route path="/bewertungen/meine" element={<RatingsPage tab="mine" />} />
            <Route path="/bewertungen/tags" element={<RatingsPage tab="tags" />} />
            <Route path="/kalender" element={<CalendarPage />} />
            <Route path="/benachrichtigungen" element={<NotificationsPage />} />
            {admin && <Route path="/pruefen" element={<ReviewPage />} />}
            {admin && <Route path="/aufgaben" element={<TasksPage />} />}
            <Route path="/statistik" element={<StatsPage />} />
            <Route path="/hoerverlauf" element={<ListeningPage />} />
            <Route path="/einstellungen" element={<SettingsPage />} />
            <Route path="*" element={<div className="p-8"><Empty title={t('Seite nicht gefunden')} text={t('Diese Adresse gibt es nicht. Über die Navigation kommst du zurück.')} /></div>} />
          </Routes>
        </div>
      </div>
      <BottomBar areas={areas} counts={counts} admin={admin} current={area?.key} />
    </div>
  )
}

function ItemCount({ k, counts }: { k: CountKey; counts: Counts }) {
  const users = k === 'review' ? counts.reviewUsers : 0
  return <Count n={counts[k]} tone={users > 0 ? 'user' : undefined}
    label={`${counts[k]} ${COUNT_LABEL[k]}${users > 0 ? t(', {{v0}} davon für Benutzer', { v0: users }) : ''}`} />
}

/** Desktop: Gruppen mit Überschrift, Zähler rechtsbündig (Vorschlag 3). */
function SideBar({ areas, counts }: { areas: Area[]; counts: Counts }) {
  const { pathname } = useLocation()
  return (
    <nav aria-label={t('Hauptnavigation')}
      className="sticky top-[60px] hidden h-[calc(100dvh-60px)] w-56 shrink-0 overflow-y-auto border-r border-line px-3 pb-4 pt-3 [scrollbar-width:thin] lg:block">
      {areas.map(a => (
        <div key={a.key} className="mb-3">
          <h2 className="px-3 pb-1 font-[family-name:var(--font-sans)] text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">{a.label}</h2>
          <ul>
            {a.items.map(it => {
              const on = isOn(it, pathname)
              const Icon = it.icon
              return (
                <li key={it.to}>
                  <Link to={it.to} aria-current={on ? 'page' : undefined}
                    className={`flex min-h-8 items-center gap-2.5 rounded-lg px-3 text-sm font-medium transition-colors duration-150 ${
                      on ? 'bg-surface-2 text-text' : 'text-muted hover:bg-surface hover:text-text'}`}>
                    <Icon size={16} aria-hidden className={on ? 'text-accent' : ''} />
                    <span className="flex-1 truncate">{it.label}</span>
                    {it.count && <ItemCount k={it.count} counts={counts} />}
                  </Link>
                </li>
              )
            })}
          </ul>
        </div>
      ))}
    </nav>
  )
}

/** Handy/Tablet: Register des aktuellen Bereichs unter dem Kopf. */
function AreaTabs({ area, counts }: { area: Area; counts: Counts }) {
  const { pathname } = useLocation()
  return (
    <nav aria-label={area.label} className="flex shrink-0 gap-5 overflow-x-auto border-b border-line bg-bg px-4 [scrollbar-width:none] sm:px-6 lg:hidden">
      {area.items.map(it => {
        const on = isOn(it, pathname)
        return (
          <Link key={it.to} to={it.to} aria-current={on ? 'page' : undefined} className={tabClass(on)}>
            {it.label}
            {it.count && <ItemCount k={it.count} counts={counts} />}
          </Link>
        )
      })}
    </nav>
  )
}

/** Untere Leiste für Handy und Tablet: fünf Bereiche, ohne «Mehr». */
function BottomBar({ areas, counts, admin, current }: { areas: Area[]; counts: Counts; admin: boolean; current?: string }) {
  const keys = admin ? BAR_ADMIN : BAR_USER
  const bar = keys.map(k => areas.find(a => a.key === k)!).filter(Boolean)
  return (
    <nav aria-label={t('Bereiche')} className="z-30 flex shrink-0 border-t border-line bg-bg/95 backdrop-blur lg:hidden">
      {bar.map(a => {
        const Icon = a.icon
        const n = a.items.reduce((s, it) => s + (it.count ? counts[it.count] : 0), 0)
        const on = current === a.key
        return (
          <Link key={a.key} to={on ? a.items[0].to : lastVisit[a.key] ?? a.items[0].to} aria-current={on ? 'page' : undefined}
            className={`relative flex min-h-14 flex-1 flex-col items-center justify-center gap-0.5 text-[11px] font-medium transition-colors duration-150 ${
              on ? 'text-accent' : 'text-muted hover:text-text'}`}>
            <span className="relative">
              <Icon size={22} aria-hidden />
              {n > 0 && <span className="absolute -right-3.5 -top-1.5"><Count n={n} tone={a.items.some(it => it.count === 'review') && counts.reviewUsers > 0 ? 'user' : undefined} /></span>}
            </span>
            {a.label}
          </Link>
        )
      })}
    </nav>
  )
}

/** Profilmenü oben rechts: Einstellungen, Design und Abmelden. */
function ProfileMenu({ theme, setTheme }: { theme: string; setTheme: (tx: string) => void }) {
  const me = useMe()
  const { pathname } = useLocation()
  const [openAt, setOpenAt] = useState<string | null>(null)
  const open = openAt === pathname
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpenAt(null) }
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpenAt(null) }
    document.addEventListener('mousedown', away); window.addEventListener('keydown', key)
    return () => { document.removeEventListener('mousedown', away); window.removeEventListener('keydown', key) }
  }, [open])
  const logout = async () => { await api('/auth/logout', { method: 'POST' }); window.location.reload() }
  const row = 'flex min-h-11 w-full items-center gap-3 rounded-lg px-3 text-left text-sm font-medium text-muted transition-colors hover:bg-surface-2 hover:text-text'
  return (
    <div ref={box} className="relative shrink-0">
      <button type="button" onClick={() => setOpenAt(open ? null : pathname)} aria-expanded={open} aria-haspopup="true"
        aria-label={t('Profil und Einstellungen')} title={t('Profil und Einstellungen')}
        className={`flex h-11 items-center gap-2 rounded-lg px-2.5 transition-colors hover:bg-surface hover:text-text ${pathname === '/einstellungen' ? 'text-accent' : 'text-muted'}`}>
        <UserRound size={18} aria-hidden />
        {me.user && <span className="hidden max-w-32 truncate text-sm font-medium sm:inline">{me.user}</span>}
      </button>
      {open && (
        <div role="menu" className="absolute right-0 top-12 z-40 w-60 rounded-2xl border border-line bg-surface p-2 shadow-2xl">
          <Link role="menuitem" to="/einstellungen" className={row}><Settings size={17} aria-hidden />{t('Einstellungen')}</Link>
          <button role="menuitem" type="button" className={row} onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
            {theme === 'dark' ? <Sun size={17} aria-hidden /> : <Moon size={17} aria-hidden />}{theme === 'dark' ? t('Helles Design') : t('Dunkles Design')}
          </button>
          <button role="menuitem" type="button" className={row} onClick={() => setUiLang(uiLang() === 'de' ? 'en' : 'de')}>
            <Languages size={17} aria-hidden />{uiLang() === 'de' ? 'English' : 'Deutsch'}
          </button>
          {me.user && <button role="menuitem" type="button" className={row} onClick={logout}><LogOut size={17} aria-hidden />{t('Abmelden')}</button>}
          <p className="px-3 pb-1 pt-2 text-xs text-muted">AudioBook Wizarrd {__APP_VERSION__}</p>
        </div>
      )}
    </div>
  )
}
