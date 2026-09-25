/**
 * Texte rund um ein Profil: die Zeile unter der Fassung, Sprachen, Rollen, Punkte, Hinweise.
 * Die Schluessel stehen woertlich da, damit `keys.test.ts` sie sieht.
 */

import type { i18n as I18n, TFunction } from 'i18next'

import type { ApiError } from '../../api/client'
import type { ProfileAnswers, ProfileLine, ProfileWarning, QuestionList } from '../../api/types'
import { formatList, formatNumber } from '../../lib/format'
import { isLanguageList, visibleIds } from './questions'

/** Die Hinweise, die der Server zur Zusammenfassung schicken kann. */
export const WARNING_CODES = ['size_limit_below_minimum', 'answer_without_effect'] as const

/** Die ersten sieben Zeichen eines Commits, wie GitHub sie zeigt. */
export function shortCommit(commit: unknown): string {
  return typeof commit === 'string' ? commit.slice(0, 7) : ''
}

/** Die Rolle als Beschriftung eines Knopfes, grossgeschrieben: "Pflicht", "Bevorzugt". */
export function roleButtonText(t: TFunction, role: string): string {
  if (role === 'required') return t('profiles.roleButtons.required')
  if (role === 'preferred') return t('profiles.roleButtons.preferred')
  return role
}

/**
 * Die Kurzzeile aus Antworten, fuer den Ueberblick im Assistenten. HDR steht nur da, wenn die
 * Frage gerade gestellt wird, wie in `profile_line` vom Server.
 */
export function lineFromAnswers(list: QuestionList, answers: ProfileAnswers): ProfileLine | null {
  if (typeof answers.resolution !== 'string') return null
  return {
    resolution: answers.resolution,
    source: typeof answers.source === 'string' ? answers.source : '',
    languages: isLanguageList(answers.languages) ? answers.languages : [],
    hdr: visibleIds(list, answers).has('hdr') && typeof answers.hdr === 'string' ? answers.hdr : null,
    max_gb_per_hour: typeof answers.max_gb_per_hour === 'number' ? answers.max_gb_per_hour : null,
    outdated: false,
  }
}

export function languageName(t: TFunction, code: string): string {
  switch (code) {
    case 'de':
      return t('profiles.languageNames.de')
    case 'en':
      return t('profiles.languageNames.en')
    case 'fr':
      return t('profiles.languageNames.fr')
    case 'es':
      return t('profiles.languageNames.es')
    case 'it':
      return t('profiles.languageNames.it')
    case 'tr':
      return t('profiles.languageNames.tr')
    // Seit 18.09.2026: die Originalsprache des Titels, welche es auch ist.
    case 'original':
      return t('profiles.languageNames.original')
    default:
      return code.toUpperCase()
  }
}

export function roleText(t: TFunction, role: string): string {
  if (role === 'required') return t('profiles.roles.required')
  if (role === 'preferred') return t('profiles.roles.preferred')
  return role
}

/** Punkte mit Vorzeichen, etwa "+11.000" oder "-10.000". */
export function formatScore(score: number, language: string): string {
  return new Intl.NumberFormat(language, { signDisplay: 'exceptZero' }).format(score)
}

/** GB je Stunde: ganze Zahlen ohne Nachkommastelle, sonst eine. */
export function formatGbPerHour(value: number, language: string): string {
  return formatNumber(value, language, Number.isInteger(value) || value >= 100 ? 0 : 1)
}

function resolutionText(t: TFunction, resolution: string): string {
  if (resolution === '2160p') return t('profiles.line.resolution2160p')
  if (resolution === '1080p') return t('profiles.line.resolution1080p')
  return resolution
}

/** Die uebliche Quelle (Encodes) steht nicht in der Zeile, nur die besonderen. */
function sourceText(t: TFunction, source: string): string | null {
  if (source === 'encodes' || source === '') return null
  if (source === 'remux') return t('profiles.line.sourceRemux')
  if (source === 'web') return t('profiles.line.sourceWeb')
  return source
}

function hdrText(t: TFunction, hdr: string): string | null {
  if (hdr === 'safety') return t('profiles.line.hdrSafety')
  if (hdr === 'free') return t('profiles.line.hdrFree')
  return null
}

/** Die Kurzform eines Profils, etwa "4K, Deutsch Pflicht, HDR bevorzugt, bis 20 GB je Stunde". */
export function profileLineText(t: TFunction, line: ProfileLine, language: string): string {
  const resolution = resolutionText(t, line.resolution)
  const source = sourceText(t, line.source)
  const parts = [source ? t('profiles.line.withSource', { resolution, source }) : resolution]
  for (const role of ['required', 'preferred'] as const) {
    const names = line.languages.filter((entry) => entry.role === role).map((entry) => languageName(t, entry.code))
    if (names.length > 0) parts.push(t('profiles.line.languages', { languages: formatList(names, language), role: roleText(t, role) }))
  }
  const hdr = line.hdr ? hdrText(t, line.hdr) : null
  if (hdr) parts.push(hdr)
  parts.push(line.max_gb_per_hour !== null ? t('profiles.line.limit', { value: formatGbPerHour(line.max_gb_per_hour, language) }) : t('profiles.line.noLimit'))
  return parts.join(', ')
}

/**
 * Werte einer Liste aus einem Fehler oder Hinweis als Texte. Der Server schickt Namen oder
 * Kennungen; kommt ein Objekt, zaehlt sein Name.
 */
export function listValues(value: unknown): string[] {
  const items = Array.isArray(value) ? value : value === undefined || value === null ? [] : [value]
  return items.flatMap((item) => {
    if (typeof item === 'string' || typeof item === 'number') return [String(item)]
    if (item && typeof item === 'object') {
      const record = item as Record<string, unknown>
      for (const key of ['label', 'name', 'field', 'question', 'id', 'code']) {
        if (typeof record[key] === 'string' || typeof record[key] === 'number') return [String(record[key])]
      }
    }
    return []
  })
}

/** Der kurze Name einer Frage, fuer Fehler und Listen. Kennt die Oberflaeche die Frage nicht, steht ihre Kennung da. */
export function questionShortName(t: TFunction, i18n: I18n, id: string): string {
  const key = `profiles.questions.${id}.short`
  return i18n.exists(key) ? t(key) : id
}

/** Mehrere Fragen mit ihren kurzen Namen, als Aufzaehlung. */
export function questionNames(t: TFunction, i18n: I18n, ids: unknown, language: string): string {
  return formatList(
    listValues(ids).map((id) => questionShortName(t, i18n, id)),
    language,
  )
}

/** `profile_answers_invalid {fields}` in Worten, mit den Namen der Fragen. */
export function invalidAnswersText(t: TFunction, i18n: I18n, error: ApiError, language: string): string {
  return listValues(error.values.fields).length > 0 ? t('profiles.invalid.fields', { fields: questionNames(t, i18n, error.values.fields, language) }) : t('profiles.invalid.plain')
}

export function warningText(t: TFunction, i18n: I18n, warning: ProfileWarning, language: string): string {
  switch (warning.code) {
    case 'size_limit_below_minimum':
      return t('profiles.warnings.size_limit_below_minimum', { qualities: formatList(listValues(warning.qualities), language) })
    case 'answer_without_effect':
      return t('profiles.warnings.answer_without_effect', { questions: questionNames(t, i18n, warning.questions, language) })
    default:
      return t('profiles.warnings.unknown', { code: warning.code })
  }
}
