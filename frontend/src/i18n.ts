/** Sprache der Oberfläche (react-i18next). Schlüssel sind die deutschen Texte selbst: fehlt eine Übersetzung, erscheint
 *  der deutsche Text. Englisch steht in locales/en.json. Die Sprache gilt je Benutzer und Gerät: gespeicherte Wahl, sonst
 *  die Sprache des Browsers, sonst Englisch. */
import i18next from 'i18next'
import { initReactI18next } from 'react-i18next'
import en from './locales/en.json'

export type UiLang = 'de' | 'en'
const KEY = 'abw-ui-lang'

function stored(): UiLang | null {
  try { const v = localStorage.getItem(KEY); return v === 'de' || v === 'en' ? v : null } catch { return null }
}

export function detect(): UiLang {
  return stored() ?? ((navigator.languages?.[0] ?? navigator.language ?? 'en').toLowerCase().startsWith('de') ? 'de' : 'en')
}

i18next.use(initReactI18next).init({
  resources: { en: { translation: en }, de: { translation: {} } },
  lng: detect(),
  fallbackLng: false,
  keySeparator: false,
  nsSeparator: false,
  interpolation: { escapeValue: false },
  returnEmptyString: false,
})
document.documentElement.lang = i18next.language

/** Text übersetzen. Platzhalter: t('{{n}} Bände', { n }) */
export const t = (text: string, vars?: Record<string, unknown>): string => (vars ? i18next.t(text, vars as never) : i18next.t(text)) as string

/** Zahlen- und Datumsformat zur Sprache der Oberfläche */
export const locale = (): string => (i18next.language === 'de' ? 'de-CH' : 'en-GB')

export const uiLang = (): UiLang => (i18next.language === 'de' ? 'de' : 'en')

/** Sprache wechseln, merken und neu laden (Texte ausserhalb von Komponenten werden beim Laden gesetzt). */
export function setUiLang(lang: UiLang): void {
  try { localStorage.setItem(KEY, lang) } catch { /* privat: nur für diese Sitzung */ }
  location.reload()
}

export default i18next
