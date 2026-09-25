/**
 * Die Saetze zu Lidarrs Benennung, ohne React: die Beschriftung je Muster, nexcrates Einwand und die Hinweise, was
 * Lidarr anders macht (Rueckmeldung 20.09.2026).
 */

import type { TFunction } from 'i18next'

import { ApiError, errorText } from '../../api/client'
import type { MusicNamingPatterns, NamingProblem, RadarrNote } from '../../api/types'

export type MusicPattern = keyof MusicNamingPatterns

export const MUSIC_PATTERNS: readonly MusicPattern[] = ['artist_folder', 'album_folder', 'track_file', 'multi_disc_file']

/** Der Einwand gegen ein Muster als Satz, wie ein Fehler des Servers. */
export function musicProblemText(t: TFunction, problem: NamingProblem | null | undefined): string | null {
  if (!problem || typeof problem.code !== 'string') return null
  // Der Einwand kam in der Antwort mit, nicht als Fehler: Status und Nummer gibt es dazu nicht.
  return errorText(t, new ApiError(422, problem.code, problem.values ?? {}))
}

/** Die Beschriftung eines Musters, wie unter Ordner und Benennung. */
export function musicPatternLabel(t: TFunction, which: MusicPattern): string {
  switch (which) {
    case 'artist_folder':
      return t('settings.files.musicNaming.patterns.artist_folder')
    case 'album_folder':
      return t('settings.files.musicNaming.patterns.album_folder')
    case 'track_file':
      return t('settings.files.musicNaming.patterns.track_file')
    default:
      return t('settings.files.musicNaming.patterns.multi_disc_file')
  }
}

export type MusicNamingNotes = { patterns: Partial<Record<MusicPattern, string[]>>; general: string[] }

/**
 * Was Lidarr bei der Benennung anders macht, als Saetze. Ohne Umbenennen behalten Dateien den Namen, mit dem sie
 * kamen; das steht bei beiden Dateimustern. Keiner der Hinweise sperrt die Uebernahme.
 */
export function musicNamingNotes(t: TFunction, notes: readonly RadarrNote[] | undefined): MusicNamingNotes {
  const result: MusicNamingNotes = { patterns: {}, general: [] }
  const add = (which: MusicPattern, text: string) => {
    result.patterns[which] = [...(result.patterns[which] ?? []), text]
  }
  for (const note of Array.isArray(notes) ? notes : []) {
    switch (note?.code) {
      case 'lidarr_no_rename': {
        const text = t('settings.files.lidarrNaming.notes.lidarr_no_rename')
        add('track_file', text)
        add('multi_disc_file', text)
        break
      }
      case 'slash_in_pattern': {
        const text = t('settings.files.lidarrNaming.notes.slash_in_pattern')
        const which = MUSIC_PATTERNS.find((pattern) => pattern === note.values?.pattern)
        if (which !== undefined) add(which, text)
        else result.general.push(text)
        break
      }
      case 'colon_format':
        result.general.push(t('settings.files.lidarrNaming.notes.colon_format'))
        break
      case 'illegal_characters_kept':
        result.general.push(t('settings.files.lidarrNaming.notes.illegal_characters_kept'))
        break
    }
  }
  return result
}
