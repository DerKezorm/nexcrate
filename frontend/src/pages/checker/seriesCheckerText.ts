/**
 * Die Saetze zum Serien-Pruefer. Der Server schickt Formen, Gruende und Zustaende als Codes, nie als
 * Saetze; hier werden sie zu Text. Die Gruende, die Filme und Serien teilen, kommen aus `checkerText`.
 */

import type { TFunction } from 'i18next'

import type { ParsedSeriesRelease, ReleaseRejection, SeriesEpisodeResult, SeriesMatch, SeriesNote, SeriesReleaseResult } from '../../api/types'
import { formatList, formatNumber } from '../../lib/format'
import { listValues } from '../profiles/profileText'
import { rejectionText, type FitState } from './checkerText'

/** Die Formen, die nexcrate erkennt, aber nicht nimmt. Jede hat ihren eigenen Satz. */
export const REFUSED_CODES = [
  'anime_numbering',
  'multi_season',
  'complete_series',
  'split_episode',
  'partial_season',
  'season_extras',
  'no_numbering',
  'implausible_range',
] as const

/** Die Namen aus dem Feld: je Zeile einer, leere Zeilen fliegen raus. */
export function namesOf(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line !== '')
}

/** Die Zustaende einer Folge, wenn nexcrate das Release naehme. */
export const EPISODE_STATES = ['fills', 'replaces', 'keeps', 'not_watched'] as const

/**
 * Wie ein Ergebnis dasteht. "Vorerst" heisst auch bei Serien: Es passt, liegt aber unter dem Ziel.
 * Steht es mit dem Ziel in einer Gruppe, gilt derselbe Zustand mit einem anderen Satz (Entscheidung 28).
 */
export function seriesFitState(result: SeriesReleaseResult): FitState {
  if (!result.accepted) return 'fitsNot'
  return result.below_target ? 'forNow' : 'fits'
}

/** Was nexcrate im Namen gefunden hat, als eine Zeile: Staffel und Folgen, ein Datum oder ein Paket. */
export function readText(t: TFunction, parsed: ParsedSeriesRelease, language: string): string {
  const season = parsed.season
  if (parsed.air_date !== null) {
    const day = t('checker.series.read.daily', { date: parsed.air_date })
    return parsed.part !== null ? `${day}, ${t('checker.series.read.part', { part: parsed.part })}` : day
  }
  if (parsed.seasons.length > 0) return t('checker.series.read.seasons', { seasons: formatList(parsed.seasons.map(String), language) })
  if (parsed.absolute.length > 0) return t('checker.series.read.absolute', { numbers: formatList(parsed.absolute.map(String), language) })
  if (parsed.episodes.length === 1 && season !== null) return t('checker.series.read.episode', { season, episode: parsed.episodes[0] })
  if (parsed.episodes.length > 1 && season !== null) {
    return t('checker.series.read.episodes', { season, first: parsed.episodes[0], last: parsed.episodes[parsed.episodes.length - 1] })
  }
  if (parsed.episodes.length === 1) return t('checker.series.read.episode', { season: 1, episode: parsed.episodes[0] })
  if (parsed.form === 'season_pack' && season !== null) return t('checker.series.read.season', { season })
  return t('checker.series.read.nothing')
}

/** Der Release-Typ in Worten, oder null bei einer Form, die nexcrate nicht nimmt. */
export function releaseTypeText(t: TFunction, parsed: ParsedSeriesRelease): string | null {
  if (parsed.release_type === 'single_episode') return t('checker.series.read.single_episode')
  if (parsed.release_type === 'multi_episode') return t('checker.series.read.multi_episode')
  if (parsed.release_type === 'season_pack') return t('checker.series.read.season_pack')
  return null
}

/** Der Satz zu einer abgelehnten Form. Null, wenn die Form genommen wird. */
export function refusedText(t: TFunction, parsed: ParsedSeriesRelease, language: string): string | null {
  switch (parsed.refused) {
    case null:
      return null
    case 'anime_numbering':
      return t('checker.series.refused.anime_numbering', { numbers: formatList(parsed.absolute.map(String), language) })
    case 'multi_season':
      return t('checker.series.refused.multi_season')
    case 'complete_series':
      return t('checker.series.refused.complete_series')
    case 'split_episode':
      return t('checker.series.refused.split_episode')
    case 'partial_season':
      return t('checker.series.refused.partial_season')
    case 'season_extras':
      return t('checker.series.refused.season_extras')
    case 'no_numbering':
      return t('checker.series.refused.no_numbering')
    case 'implausible_range':
      return t('checker.series.refused.implausible_range')
    default:
      return t('checker.series.refused.unknown', { code: parsed.refused })
  }
}

/** Ueber welches Schema die Folgen gefunden wurden. */
export function viaText(t: TFunction, via: string | null): string | null {
  switch (via) {
    case null:
      return null
    case 'scene':
      return t('checker.series.match.scene')
    case 'tvdb':
      return t('checker.series.match.tvdb')
    case 'tmdb':
      return t('checker.series.match.tmdb')
    case 'air_date':
      return t('checker.series.match.air_date')
    // Seit S3: lokale Korrektur und gewaehlte Episodengruppe.
    case 'owner':
      return t('checker.series.match.owner')
    case 'group':
      return t('checker.series.match.group')
    // Seit Anime A2: die Durchzaehlung einer Anime-Serie.
    case 'absolute':
      return t('checker.series.match.absolute')
    default:
      return t('checker.series.match.unknown', { via })
  }
}

/** Der Name eines Schemas als Satzanfang, etwa "Die TMDB-Nummer". */
export function schemeName(t: TFunction, via: string | null): string {
  switch (via) {
    case 'scene':
      return t('checker.series.match.name.scene')
    case 'tvdb':
      return t('checker.series.match.name.tvdb')
    case 'tmdb':
      return t('checker.series.match.name.tmdb')
    case 'air_date':
      return t('checker.series.match.name.air_date')
    case 'owner':
      return t('checker.series.match.name.owner')
    case 'group':
      return t('checker.series.match.name.group')
    case 'absolute':
      return t('checker.series.match.name.absolute')
    default:
      return t('checker.series.match.name.unknown', { via: via ?? '' })
  }
}

/** Eine Nummerierung als Teil eines Satzes: "zaehlt nach TVDB", "zaehlt nach der Szene-Nummerierung". */
export function countingName(t: TFunction, via: string | null): string {
  switch (via) {
    case 'scene':
      return t('checker.series.match.counting.scene')
    case 'tvdb':
      return t('checker.series.match.counting.tvdb')
    case 'tmdb':
      return t('checker.series.match.counting.tmdb')
    case 'group':
      return t('checker.series.match.counting.group')
    default:
      return t('checker.series.match.counting.unknown', { via: via ?? '' })
  }
}

/** Die Hinweise zur Zuordnung: nicht eindeutig, fehlende Nummern, unbestaetigte Szene-Nummer, Zaehlweise der Gruppe. */
export function matchNotes(t: TFunction, match: Pick<SeriesMatch, 'ambiguous' | 'other' | 'missing' | 'notes' | 'via'>, language: string): string[] {
  const lines: string[] = []
  if (match.ambiguous && match.other) {
    lines.push(
      t('checker.series.match.ambiguous', {
        other: schemeName(t, typeof match.other.via === 'string' ? match.other.via : null),
        codes: formatList(listValues(match.other.codes), language),
      }),
    )
  }
  if (match.missing.length > 0) lines.push(t('checker.series.match.missing', { codes: formatList(match.missing.map(String), language) }))
  for (const note of match.notes) {
    if (note === 'unverified_scene') lines.push(t('checker.series.match.unverified_scene'))
    if (note === 'two_dates') lines.push(t('checker.series.match.two_dates'))
    if (note === 'not_daily') lines.push(t('checker.series.match.not_daily'))
    if (note === 'not_anime') lines.push(t('checker.series.match.not_anime'))
    if (note === 'group_counting') lines.push(t('checker.series.match.group_counting', { scheme: countingName(t, match.via ?? null) }))
  }
  return lines
}

/** Ein Grund, warum ein Release nicht passt. Die Codes der Filme gelten weiter. */
export function seriesRejectionText(t: TFunction, rejection: ReleaseRejection, language: string): string {
  switch (rejection.code) {
    case 'refused_form':
      return t('checker.series.rejections.refused_form')
    case 'no_episode_match':
      return t('checker.series.rejections.no_episode_match')
    case 'season_incomplete': {
      const count = listValues(rejection.not_aired).length
      return t('checker.series.rejections.season_incomplete', { count, value: formatNumber(count, language) })
    }
    case 'already_imported':
      return t('checker.series.rejections.already_imported')
    default:
      return rejectionText(t, rejection, language)
  }
}

/** Ein Hinweis, der nichts ablehnt. */
export function seriesNoteText(t: TFunction, note: SeriesNote): string {
  if (note.code === 'runtime_unknown') return t('checker.series.notes.runtime_unknown')
  if (note.code === 'title_mismatch') return t('checker.series.notes.title_mismatch')
  return t('checker.series.notes.unknown', { code: note.code })
}

/** Was mit einer Folge geschaehe. */
export function episodeStateText(t: TFunction, row: SeriesEpisodeResult): string {
  switch (row.state) {
    case 'fills':
      return t('checker.series.episodes.fills')
    case 'replaces':
      return t('checker.series.episodes.replaces')
    case 'not_watched':
      return t('checker.series.episodes.not_watched')
    default: {
      const reason = row.upgrade?.reason ?? null
      if (reason === 'file_covers_more') return t('checker.series.episodes.keeps', { reason: t('checker.series.episodes.file_covers_more') })
      const text = reason === null ? null : keepReason(t, reason)
      return text === null ? t('checker.series.episodes.keepsPlain') : t('checker.series.episodes.keeps', { reason: text })
    }
  }
}

function keepReason(t: TFunction, reason: string): string | null {
  switch (reason) {
    case 'worse_quality':
      return t('checker.upgrade.reasons.worse_quality')
    case 'upgrades_disabled':
      return t('checker.upgrade.reasons.upgrades_disabled')
    case 'cutoff_met':
      return t('checker.upgrade.reasons.cutoff_met')
    case 'score_not_higher':
      return t('checker.upgrade.reasons.score_not_higher')
    // Die vorhandene Datei stammt aus genau diesem Release (seit S4, wie Sonarrs "schon importiert").
    case 'same_release':
      return t('checker.upgrade.reasons.same_release')
    case 'upgrade_until_reached':
      return t('checker.upgrade.reasons.upgrade_until_reached')
    case 'step_too_small':
      return t('checker.upgrade.reasons.step_too_small')
    default:
      return null
  }
}
