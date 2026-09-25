/**
 * Die Saetze zu Sonarrs Benennung, ohne React: die Beschriftung je Muster, nexcrates Einwand, Sonarrs Stil fuer
 * mehrere Folgen in einer Datei und die Hinweise, was Sonarr anders macht (S6, Entscheidung 13).
 */

import type { TFunction } from 'i18next'

import { ApiError, errorText } from '../../api/client'
import type { NamingProblem, RadarrNote, SeriesNamingPatterns } from '../../api/types'
import { SERIES_PATTERNS } from './takeoverText'

export type SeriesPattern = keyof SeriesNamingPatterns

/** Der Einwand gegen ein Muster als Satz, wie ein Fehler des Servers. */
export function patternProblemText(t: TFunction, problem: NamingProblem | null | undefined): string | null {
  if (!problem || typeof problem.code !== 'string') return null
  // Der Einwand kam in der Antwort mit, nicht als Fehler: Status und Nummer gibt es dazu nicht.
  return errorText(t, new ApiError(422, problem.code, problem.values ?? {}))
}

/** Die Beschriftung eines Musters, wie unter Ordner und Benennung. */
export function patternLabel(t: TFunction, which: SeriesPattern): string {
  switch (which) {
    case 'series_folder':
      return t('settings.files.seriesNaming.patterns.series_folder')
    case 'season_folder':
      return t('settings.files.seriesNaming.patterns.season_folder')
    case 'specials_folder':
      return t('settings.files.seriesNaming.patterns.specials_folder')
    case 'episode_file':
      return t('settings.files.seriesNaming.patterns.episode_file')
    case 'daily_file':
      return t('settings.files.seriesNaming.patterns.daily_file')
    default:
      return t('settings.files.seriesNaming.patterns.anime_file')
  }
}

/** Sonarrs Stil fuer mehrere Folgen in einer Datei als Beispiel. Einen unbekannten Stil nennt die Seite nicht. */
export function styleText(t: TFunction, style: string | null | undefined): string | null {
  switch (style) {
    case 'extend':
      return t('import.sonarrNaming.styles.extend')
    case 'duplicate':
      return t('import.sonarrNaming.styles.duplicate')
    case 'repeat':
      return t('import.sonarrNaming.styles.repeat')
    case 'scene':
      return t('import.sonarrNaming.styles.scene')
    case 'range':
      return t('import.sonarrNaming.styles.range')
    case 'prefixed_range':
      return t('import.sonarrNaming.styles.prefixed_range')
    default:
      return null
  }
}

/** Die Hinweise zu Sonarrs Benennung, getrennt nach dem Muster, zu dem sie gehoeren. Unbekannte Codes fallen weg. */
export type SeriesNamingNotes = { patterns: Partial<Record<SeriesPattern, string[]>>; general: string[] }

/**
 * Was Sonarr bei der Benennung anders macht, als Saetze. Ohne Umbenennen behalten Dateien den Release-Namen, das steht
 * bei beiden Dateimustern; `slash_in_pattern` steht beim Muster aus `values.pattern`, der Rest allgemein. Keiner
 * sperrt die Uebernahme.
 */
export function seriesNamingNotes(t: TFunction, notes: readonly RadarrNote[] | undefined): SeriesNamingNotes {
  const result: SeriesNamingNotes = { patterns: {}, general: [] }
  const add = (which: SeriesPattern, text: string) => {
    result.patterns[which] = [...(result.patterns[which] ?? []), text]
  }
  for (const note of Array.isArray(notes) ? notes : []) {
    switch (note?.code) {
      case 'sonarr_no_rename': {
        const text = t('import.sonarrNaming.notes.sonarr_no_rename')
        add('episode_file', text)
        add('daily_file', text)
        break
      }
      case 'slash_in_pattern': {
        const text = t('import.sonarrNaming.notes.slash_in_pattern')
        const which = SERIES_PATTERNS.find((pattern) => pattern === note.values?.pattern)
        if (which !== undefined) add(which, text)
        else result.general.push(text)
        break
      }
      case 'colon_format':
        result.general.push(t('import.sonarrNaming.notes.colon_format'))
        break
      case 'illegal_characters_kept':
        result.general.push(t('import.sonarrNaming.notes.illegal_characters_kept'))
        break
      case 'style_differs':
        result.general.push(t('import.sonarrNaming.notes.style_differs'))
        break
    }
  }
  return result
}
