/**
 * Die Sprachen der Oberflaeche, an genau einer Stelle.
 *
 * Neue Sprache: den Ordner `src/i18n/<code>/` mit denselben Dateien wie `de/`
 * anlegen und hier eine Zeile eintragen. Mehr nicht. Auswahllisten, Start und
 * Tests lesen alles von hier. `languages.test.ts` prueft, dass Ordner und Liste
 * zusammenpassen.
 *
 * Der Name steht in der Sprache selbst, damit jeder seine eigene findet.
 */

export const LANGUAGES = [
  { code: 'de', name: 'Deutsch' },
  { code: 'en', name: 'English' },
] as const satisfies readonly { code: string; name: string }[]

export type Language = (typeof LANGUAGES)[number]['code']

/** Gilt, wenn der Browser keine der Sprachen oben nennt. */
export const FALLBACK_LANGUAGE: Language = 'en'

export function isLanguage(value: unknown): value is Language {
  return LANGUAGES.some((language) => language.code === value)
}

/**
 * Die erste Sprache aus den Browser-Einstellungen, die es hier gibt. Erst der
 * genaue Code, dann der Hauptteil: `de-AT` zaehlt als `de`.
 */
export function browserLanguage(preferred: readonly string[]): Language {
  for (const tag of preferred) {
    const lower = tag.toLowerCase()
    const exact = LANGUAGES.find((language) => language.code.toLowerCase() === lower)
    if (exact) return exact.code
    const base = LANGUAGES.find((language) => language.code.toLowerCase() === lower.split('-')[0])
    if (base) return base.code
  }
  return FALLBACK_LANGUAGE
}
