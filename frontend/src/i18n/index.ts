/**
 * Die Texte liegen je Bereich in einem Paar Dateien: `de/<bereich>.json` und
 * `en/<bereich>.json`. Der Dateiname wird zum ersten Teil des Schluessels, aus
 * `downloads.json` mit `title` wird `downloads.title`. So koennen mehrere Leute
 * an verschiedenen Bereichen schreiben, ohne sich in einer Datei zu begegnen.
 *
 * Welche Sprachen es gibt, steht allein in `languages.ts`.
 *
 * Geladen wird nur die Sprache, die gerade gilt. Es gibt keine Rueckfallsprache:
 * Fehlt ein Text, erscheint sein Schluessel. `complete.test.ts` haelt alle
 * Sprachen gleich, `keys.test.ts` prueft die Schluessel im Code.
 */

import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

import { browserLanguage, isLanguage, type Language } from './languages'

export { FALLBACK_LANGUAGE, isLanguage, LANGUAGES } from './languages'
export type { Language } from './languages'

const STORAGE_KEY = 'nexcrate.language'

const LOADERS = import.meta.glob<{ default: Record<string, unknown> }>('./*/*.json')

/** Vor der Anmeldung: die zuletzt gewaehlte Sprache, sonst die des Browsers. */
function initialLanguage(): Language {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (isLanguage(stored)) return stored
  } catch {
    // Privater Modus ohne localStorage.
  }
  const preferred = navigator.languages?.length ? navigator.languages : [navigator.language]
  return browserLanguage(preferred)
}

async function textsFor(language: Language): Promise<Record<string, unknown>> {
  const prefix = `./${language}/`
  const parts = await Promise.all(
    Object.entries(LOADERS)
      .filter(([path]) => path.startsWith(prefix))
      .map(async ([path, load]) => [path.slice(prefix.length, -'.json'.length), (await load()).default] as const),
  )
  return Object.fromEntries(parts)
}

export async function startI18n(language: Language = initialLanguage()): Promise<void> {
  await i18n.use(initReactI18next).init({
    resources: { [language]: { translation: await textsFor(language) } },
    lng: language,
    fallbackLng: false,
    interpolation: { escapeValue: false },
  })
  document.documentElement.lang = language
}

export async function changeLanguage(language: Language): Promise<void> {
  try {
    localStorage.setItem(STORAGE_KEY, language)
  } catch {
    // Dann gilt die Wahl nur bis zum Neuladen.
  }
  if (i18n.language === language) return
  if (!i18n.hasResourceBundle(language, 'translation')) {
    i18n.addResourceBundle(language, 'translation', await textsFor(language))
  }
  document.documentElement.lang = language
  await i18n.changeLanguage(language)
}

export default i18n
