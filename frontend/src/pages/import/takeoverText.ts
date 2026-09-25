/**
 * Die Regeln und Saetze der Uebernahme, ohne React: was die Seite an den Server schickt, was den Knopf
 * "Übernehmen und Verbindung beenden" sperrt, und die Saetze zu Phasen, Zuordnungen und Fehlern.
 *
 * Seit S6 gilt alles auch fuer eine Verbindung zu Sonarr. Die Formen sind gleich; die Saetze heissen bei Serien
 * anders, deshalb tragen die Text-Funktionen ein `series`.
 */

import type { TFunction } from 'i18next'

import { ApiError, errorText } from '../../api/client'
import type {
  PathMapping,
  RadarrNote,
  SeriesNamingPatterns,
  SeriesSourceNaming,
  Source,
  SourceNaming,
  TakeoverJob,
  TakeoverPhase,
  TakeoverRequest,
  TakeoverResult,
  TakeoverRoot,
} from '../../api/types'
import { formatNumber } from '../../lib/format'

/** Die Sperren, die die Seite kennt. Eine andere nimmt sie als Sperre, die sich nicht bestaetigen laesst. */
const KNOWN_BLOCKERS: readonly string[] = ['root_unmapped', 'files_missing', 'queue_active']

/** Die fuenf Muster, die eine Serienfassung aus Sonarr uebernimmt (S6, Entscheidung 13). */
export const SERIES_PATTERNS: readonly (keyof SeriesNamingPatterns)[] = ['series_folder', 'season_folder', 'specials_folder', 'episode_file', 'daily_file', 'anime_file']

/** Was der Besitzer im Ergebnis einer Pruefung angehakt hat. Die Bestaetigungen beginnen leer. */
export type TakeoverChoices = { missing: boolean; queue: boolean; naming: boolean; folder: boolean; keep: boolean }

/** `keep` ab Werk an (entschieden am 24.09.2026): eine Uebernahme soll keine Welle von Verbesserungen ausloesen. */
export const DEFAULT_CHOICES: TakeoverChoices = { missing: false, queue: false, naming: true, folder: true, keep: true }

/** Selbst gewaehlte Ordner: Stammordner in Radarr auf den Ordner, unter dem nexcrate ihn sieht. */
export type ChosenFolders = Readonly<Record<string, string>>

export function chosenMappings(chosen: ChosenFolders): PathMapping[] {
  return Object.entries(chosen).map(([remote, local]) => ({ remote, local }))
}

export function sameChosen(left: ChosenFolders, right: ChosenFolders): boolean {
  const keys = Object.keys(left)
  return keys.length === Object.keys(right).length && keys.every((remote) => left[remote] === right[remote])
}

export type TakeoverBlock = { reason: 'root_unmapped' | 'pending' | 'unknown' | 'confirm'; code?: string } | null

/**
 * Welche Art eine Verbindung fuellt: `false` Filme (Radarr), `true` Serien (Sonarr), seit Musik M6 `'music'` (Lidarr).
 * Die Satzfunktionen nehmen sie, wo Filme und Serien je eigene Saetze haben.
 */
export type TakeoverKind = boolean | 'music'

export function takeoverKind(app: string | undefined): TakeoverKind {
  return app === 'lidarr' ? 'music' : app === 'sonarr'
}

/** Ob die Verbindung Serien fuellt. Ein Ergebnis ohne `app` kommt aus Radarr (ein Server von vor S6). */
export function isSeriesResult(result: TakeoverResult): boolean {
  return result.app === 'sonarr'
}

/** Sonarrs Benennung hat fuenf Muster, Radarrs zwei. */
export function isSeriesNaming(naming: SourceNaming | SeriesSourceNaming | null): naming is SeriesSourceNaming {
  return naming !== null && 'episode_file' in naming
}

/**
 * Ein Ordner aus Radarr ohne Dateien (Befund 13): Radarr hat dort nur Filme ohne Datei. Die Zuordnung ist freiwillig;
 * ohne sie legt nexcrate diese Filme spaeter im Standardordner ab. Bei Sonarr gilt dasselbe fuer Serien ohne Datei.
 */
export function isOptionalRoot(root: TakeoverRoot): boolean {
  return root.files === 0
}

/**
 * Warum die Uebernahme noch nicht geht, oder null. Ein Ordner mit Dateien ohne Zuordnung sperrt, bis eine neue
 * Pruefung ihn zugeordnet hat; ein Ordner ohne Dateien nie. Ein gewaehlter, noch nicht gepruefter Ordner sperrt immer,
 * auch bei einem Ordner ohne Dateien. Fehlende Dateien und laufende Downloads in Radarr sperren, bis sie bestaetigt sind.
 */
export function takeoverBlock(result: TakeoverResult, choices: TakeoverChoices, pendingChoice: boolean): TakeoverBlock {
  // Der Server nennt `root_unmapped` selbst. Die Seite liest es auch an den Ordnern ab, damit Anzeige und Knopf zusammenpassen.
  const unmapped = result.roots.some((root) => root.local === null && !isOptionalRoot(root))
  if (unmapped || result.blockers.includes('root_unmapped')) return { reason: 'root_unmapped' }
  if (pendingChoice) return { reason: 'pending' }
  const unknown = result.blockers.find((code) => !KNOWN_BLOCKERS.includes(code))
  if (unknown !== undefined) return { reason: 'unknown', code: unknown }
  if (result.blockers.includes('files_missing') && !choices.missing) return { reason: 'confirm' }
  if (result.blockers.includes('queue_active') && !choices.queue) return { reason: 'confirm' }
  return null
}

export function blockText(t: TFunction, block: NonNullable<TakeoverBlock>, series: TakeoverKind = false): string {
  switch (block.reason) {
    case 'root_unmapped':
      if (series === 'music') return t('import.takeover.music.blocked.root_unmapped')
      return series ? t('import.takeover.series.blocked.root_unmapped') : t('import.takeover.blocked.root_unmapped')
    case 'pending':
      return t('import.takeover.blocked.pending')
    case 'unknown':
      return t('import.takeover.blocked.unknown', { code: block.code ?? '' })
    default:
      return t('import.takeover.blocked.confirm')
  }
}

/**
 * Der Koerper der Uebernahme aus dem Ergebnis der letzten Pruefung: jede Zuordnung, die sie zeigt (gefunden, gleicher
 * Pfad, gewaehlt oder abgeleitet, auch bei Ordnern ohne Dateien), die Bestaetigungen nur fuer Sperren, die es gibt, die Benennung nur, wenn nexcrate sie annimmt,
 * und den vorgeschlagenen Ordner nur fuer eine Fassung ohne Ordner.
 */
export function takeoverRequest(result: TakeoverResult, choices: TakeoverChoices): TakeoverRequest {
  const version = result.version
  return {
    mappings: result.roots.flatMap((root) => (root.local !== null ? [{ remote: root.remote, local: root.local }] : [])),
    accept_missing: result.blockers.includes('files_missing') && choices.missing,
    accept_queue: result.blockers.includes('queue_active') && choices.queue,
    take_naming: result.naming !== null && result.naming.can_take && choices.naming,
    folder: version.folder === null && version.folder_proposal !== null && choices.folder ? version.folder_proposal : null,
    keep_as_is: choices.keep,
  }
}

/**
 * Die Phase eines Auftrags als Satz. `reading_folders` gibt es nur bei Sonarr: Die Uebernahme ist gespeichert, und
 * nexcrate liest die Serienordner nach Dateien ein, die Sonarr nicht kannte. Eine unbekannte Phase sagt "Startet".
 */
export function phaseText(t: TFunction, phase: TakeoverPhase | string | null, series: TakeoverKind = false): string {
  switch (phase) {
    case 'reading':
      if (series === 'music') return t('import.takeover.music.phase.reading')
      return series ? t('import.takeover.series.phase.reading') : t('import.takeover.phase.reading')
    case 'mapping':
      return t('import.takeover.phase.mapping')
    case 'files':
      return t('import.takeover.phase.files')
    case 'saving':
      return t('import.takeover.phase.saving')
    case 'reading_folders':
      return series === 'music' ? t('import.takeover.music.phase.readingFolders') : t('import.takeover.series.phase.readingFolders')
    case 'companions':
      return t('import.takeover.phase.companions')
    default:
      return t('import.takeover.phase.starting')
  }
}

/** Was der Fortschrittsbalken zeigt: den Anteil, seine Beschriftung und die Zahlen als Satz. */
export type TakeoverProgress = { value: number; label: string; text: string }

/**
 * Der Fortschritt eines laufenden Auftrags, oder null, solange es keinen gibt. In den Phasen bis `saving` sind es die
 * angesehenen Dateien, in `reading_folders` die eingelesenen Serienordner.
 */
export function takeoverProgress(t: TFunction, job: TakeoverJob, language: string, kind: TakeoverKind = false): TakeoverProgress | null {
  const progress = job.progress
  if (progress === null || progress.total <= 0) return null
  const values = { done: formatNumber(progress.done, language), total: formatNumber(progress.total, language) }
  // ⚠️ `TakeoverPhase` in `api/types.ts` kennt die Phasen nach dem Speichern nicht; der Server schickt sie.
  const folders = (job.phase as string | null) === 'reading_folders'
  if (folders && kind === 'music') {
    return {
      value: progress.done / progress.total,
      label: t('import.takeover.music.running.foldersLabel'),
      text: t('import.takeover.music.running.folders', values),
    }
  }
  return {
    value: progress.done / progress.total,
    label: folders ? t('import.takeover.series.running.foldersLabel') : t('import.takeover.running.progressLabel'),
    text: folders ? t('import.takeover.series.running.folders', values) : t('import.takeover.running.progress', values),
  }
}

/**
 * Wie ein Stammordner zu seinem Ordner in nexcrate kam, mit den Beispieldateien. Ein Ordner ohne Dateien hat keine
 * Beispieldateien; ohne Zuordnung sagt der Satz dort, wohin diese Filme dann kommen.
 */
export function foundByText(t: TFunction, root: TakeoverRoot, language: string, series: TakeoverKind = false): string | null {
  const matched = formatNumber(root.samples_matched, language)
  const samples = formatNumber(root.samples, language)
  const optional = isOptionalRoot(root)
  switch (root.found_by) {
    case 'same_path':
      if (series === 'music') return t('import.takeover.music.roots.same_path')
      return series ? t('import.takeover.series.roots.same_path') : t('import.takeover.roots.same_path')
    case 'found':
      return t('import.takeover.roots.found', { matched, samples })
    case 'chosen':
      return optional ? t('import.takeover.roots.chosenNoFiles') : t('import.takeover.roots.chosen', { matched, samples })
    case 'derived':
      return t('import.takeover.roots.derived')
    case 'none':
      if (series === 'music') return optional ? t('import.takeover.music.roots.noneNoFiles') : t('import.takeover.music.roots.none')
      if (optional) return series ? t('import.takeover.series.roots.noneNoFiles') : t('import.takeover.roots.noneNoFiles')
      return series ? t('import.takeover.series.roots.none') : t('import.takeover.roots.none')
    default:
      return null
  }
}

/**
 * Die Hinweise aus Radarrs Einstellungen fuer Medien als Saetze: was sich mit der Uebernahme gegenueber Radarr aendert.
 * Sonarr schickt dieselben Hinweise mit eigenen Codes (S6, Entscheidung 12). Sie sperren nichts. Ein Code, den diese
 * Oberflaeche nicht kennt, faellt weg, statt roh dazustehen.
 */
export function takeoverNoteTexts(t: TFunction, notes: readonly RadarrNote[] | undefined, language: string): string[] {
  const texts: string[] = []
  for (const note of Array.isArray(notes) ? notes : []) {
    switch (note?.code) {
      case 'radarr_recycle_bin': {
        const days = note.values?.days
        texts.push(
          typeof days === 'number' && days > 0
            ? t('import.takeover.notes.radarr_recycle_bin', { count: days, value: formatNumber(days, language) })
            : t('import.takeover.notes.radarrRecycleBinNoDays'),
        )
        break
      }
      case 'radarr_extra_files':
        texts.push(t('import.takeover.notes.radarr_extra_files'))
        break
      case 'radarr_hardlinks_off':
        texts.push(t('import.takeover.notes.radarr_hardlinks_off'))
        break
      case 'radarr_file_date':
        texts.push(t('import.takeover.notes.radarr_file_date'))
        break
      case 'radarr_permissions':
        texts.push(t('import.takeover.notes.radarr_permissions'))
        break
      case 'sonarr_recycle_bin': {
        const days = note.values?.days
        texts.push(
          typeof days === 'number' && days > 0
            ? t('import.takeover.series.notes.sonarr_recycle_bin', { count: days, value: formatNumber(days, language) })
            : t('import.takeover.series.notes.recycleBinNoDays'),
        )
        break
      }
      case 'sonarr_extra_files':
        texts.push(t('import.takeover.series.notes.sonarr_extra_files'))
        break
      case 'sonarr_hardlinks_off':
        texts.push(t('import.takeover.series.notes.sonarr_hardlinks_off'))
        break
      case 'sonarr_file_date':
        texts.push(t('import.takeover.series.notes.sonarr_file_date'))
        break
      case 'sonarr_permissions':
        texts.push(t('import.takeover.series.notes.sonarr_permissions'))
        break
    }
  }
  return texts
}

/** Der Fehler eines Auftrags als Satz. Die Adresse der Verbindung steht fuer Radarrs Codes bereit, die sie nennen. */
export function jobErrorText(t: TFunction, job: TakeoverJob, source: Source): string {
  // Der Code kam mit dem Auftrag, nicht als Antwort auf eine Anfrage: Status und Nummer gibt es dazu nicht.
  return errorText(t, new ApiError(200, job.error_code ?? 'internal_error', { url: source.url, ...(job.error_values ?? {}) }))
}

/** Bei `takeover_needs_import` der Grund, warum Radarr nicht gelesen werden konnte, als eigener Satz. Sonst null. */
export function jobReasonText(t: TFunction, job: TakeoverJob, source: Source): string | null {
  const reason = job.error_values?.reason
  if (job.error_code !== 'takeover_needs_import' || typeof reason !== 'string' || reason === '') return null
  return errorText(t, new ApiError(200, reason, { url: source.url }))
}
