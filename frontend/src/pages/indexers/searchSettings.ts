/**
 * Die Einstellungen eines Indexers, die seit Schritt 2c die Suche veraendern: Prioritaet,
 * Mindestzahl an Seedern (nur Torznab), die Sprachen hinter MULTi und die Titelsuche ohne Jahr.
 * Die Schluessel stehen woertlich da, damit `keys.test.ts` sie sieht.
 */

import type { TFunction } from 'i18next'

import { DAILY_LIMIT_MAX, DAILY_LIMIT_MIN } from '../../api/indexers'
import type { Indexer, IndexerKind, IndexerSearchSettings } from '../../api/types'

export const PRIORITY_MIN = 1
export const PRIORITY_MAX = 50
export const DEFAULT_PRIORITY = 25
export const DEFAULT_MINIMUM_SEEDERS = 1
export const MINIMUM_SEEDERS_MAX = 10_000

/** Die Sprachen zur Auswahl bei MULTi, fest. Gespeichert wird in dieser Reihenfolge. */
export const MULTI_LANGUAGE_CODES = ['de', 'en', 'fr', 'es', 'it', 'nl', 'pl', 'pt', 'ru', 'tr', 'ja', 'ko', 'zh', 'sv', 'da', 'no', 'fi', 'cs', 'hu'] as const

const KNOWN: readonly string[] = MULTI_LANGUAGE_CODES

/** Der Name einer Sprache in der Sprache der Oberflaeche. Ein Code von aussen (aus Radarr geholt) bekommt den Namen des Browsers. */
export function multiLanguageName(t: TFunction, code: string, language: string): string {
  switch (code) {
    case 'de':
      return t('indexers.languages.de')
    case 'en':
      return t('indexers.languages.en')
    case 'fr':
      return t('indexers.languages.fr')
    case 'es':
      return t('indexers.languages.es')
    case 'it':
      return t('indexers.languages.it')
    case 'nl':
      return t('indexers.languages.nl')
    case 'pl':
      return t('indexers.languages.pl')
    case 'pt':
      return t('indexers.languages.pt')
    case 'ru':
      return t('indexers.languages.ru')
    case 'tr':
      return t('indexers.languages.tr')
    case 'ja':
      return t('indexers.languages.ja')
    case 'ko':
      return t('indexers.languages.ko')
    case 'zh':
      return t('indexers.languages.zh')
    case 'sv':
      return t('indexers.languages.sv')
    case 'da':
      return t('indexers.languages.da')
    case 'no':
      return t('indexers.languages.no')
    case 'fi':
      return t('indexers.languages.fi')
    case 'cs':
      return t('indexers.languages.cs')
    case 'hu':
      return t('indexers.languages.hu')
    default:
      return otherLanguageName(code, language)
  }
}

function otherLanguageName(code: string, language: string): string {
  try {
    const name = new Intl.DisplayNames([language], { type: 'language' }).of(code)
    if (name && name.toLowerCase() !== code.toLowerCase()) return name
  } catch {
    // Kein gueltiger Sprachcode. Dann steht er selbst da.
  }
  return code.toUpperCase()
}

/** Ohne Doppelte, erst die bekannten in der Reihenfolge der Liste, dann fremde alphabetisch. So haengt nichts an der Reihenfolge der Klicks. */
export function orderLanguages(codes: readonly string[]): string[] {
  const unique = [...new Set(codes)]
  const known = MULTI_LANGUAGE_CODES.filter((code) => unique.includes(code))
  const other = unique.filter((code) => !KNOWN.includes(code)).sort()
  return [...known, ...other]
}

/** Die Auswahl zum Ankreuzen: jede Sprache der Liste und dazu, was der Indexer schon hat, nach Namen sortiert. */
export function languageOptions(t: TFunction, chosen: readonly string[], language: string): { code: string; name: string }[] {
  const codes = orderLanguages([...MULTI_LANGUAGE_CODES, ...chosen])
  return codes.map((code) => ({ code, name: multiLanguageName(t, code, language) })).sort((left, right) => left.name.localeCompare(right.name, language))
}

/** Eine ganze Zahl im Bereich, sonst null. */
export function parseWhole(text: string, min: number, max: number): number | null {
  const clean = text.trim()
  if (!/^\d{1,6}$/.test(clean)) return null
  const value = Number(clean)
  return value >= min && value <= max ? value : null
}

/** Das Tageslimit aus dem Feld (Schritt 3c): leer heisst keins (null), sonst eine ganze Zahl von 1 bis 100000. Alles andere ist `invalid`. */
export function readDailyLimit(text: string): number | null | 'invalid' {
  if (text.trim() === '') return null
  return parseWhole(text, DAILY_LIMIT_MIN, DAILY_LIMIT_MAX) ?? 'invalid'
}

/** Was im Formular vorbelegt ist. Ein Server von vor 2c schickt nichts davon, dann gelten die Standardwerte. */
export function initialSearchSettings(indexer: Indexer | null): { priority: string; minimumSeeders: string; multiLanguages: string[]; removeYear: boolean } {
  return {
    priority: String(indexer?.priority ?? DEFAULT_PRIORITY),
    minimumSeeders: String(indexer?.minimum_seeders ?? DEFAULT_MINIMUM_SEEDERS),
    multiLanguages: indexer?.multi_languages ?? [],
    removeYear: indexer?.remove_year ?? false,
  }
}

/** Die Werte aus dem Formular, oder der Name des Fehlers. Mindest-Seeder gibt es nur bei Torznab, sonst null. */
export function readSearchSettings(
  kind: IndexerKind,
  form: { priority: string; minimumSeeders: string; multiLanguages: readonly string[]; removeYear: boolean },
): IndexerSearchSettings | 'priority' | 'minimumSeeders' {
  const priority = parseWhole(form.priority, PRIORITY_MIN, PRIORITY_MAX)
  if (priority === null) return 'priority'
  let minimumSeeders: number | null = null
  if (kind === 'torznab') {
    minimumSeeders = parseWhole(form.minimumSeeders, 0, MINIMUM_SEEDERS_MAX)
    if (minimumSeeders === null) return 'minimumSeeders'
  }
  return { priority, minimum_seeders: minimumSeeders, multi_languages: orderLanguages(form.multiLanguages), remove_year: form.removeYear }
}

/** Ob der Server die Einstellungen so gespeichert hat. Fehlt ein Feld in der Antwort, ist es nicht gespeichert. */
export function hasSearchSettings(saved: Partial<IndexerSearchSettings>, wanted: IndexerSearchSettings): boolean {
  return (
    saved.priority === wanted.priority &&
    saved.minimum_seeders !== undefined &&
    saved.minimum_seeders === wanted.minimum_seeders &&
    saved.remove_year === wanted.remove_year &&
    Array.isArray(saved.multi_languages) &&
    orderLanguages(saved.multi_languages).join(',') === orderLanguages(wanted.multi_languages).join(',')
  )
}
