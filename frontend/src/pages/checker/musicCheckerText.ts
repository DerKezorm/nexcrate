import type { TFunction } from 'i18next'

import type { AlbumNote, MusicStep, ReleaseRejection } from '../../api/types'
import { formatNumber } from '../../lib/format'
import { musicStepText } from '../../lib/musicSteps'
import { sizeText } from '../../lib/size'

function stepOf(t: TFunction, value: unknown): string {
  return musicStepText(t, typeof value === 'string' ? (value as MusicStep | 'unknown') : 'unknown')
}

/** Der Satz je Grund. Woertliche Schluessel, damit der Waechter sie findet; ein neuer Code zeigt seine Kennung. */
function numberOf(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

/** Seit M3 mit `language` fuer Groessen und Minuten aus der Suche; der Pruefer braucht es nicht. */
export function albumRejectionText(t: TFunction, rejection: ReleaseRejection, language = 'de'): string {
  switch (rejection.code) {
    case 'several_albums':
      return t('checker.music.rejections.several_albums')
    case 'cue_single_file':
      return t('checker.music.rejections.cue_single_file')
    case 'audiobook':
      return t('checker.music.rejections.audiobook')
    case 'unknown_quality':
      return t('checker.music.rejections.unknown_quality')
    case 'not_the_target':
      return t('checker.music.rejections.not_the_target', { step: stepOf(t, rejection.step) })
    case 'step_never_taken':
      return t('checker.music.rejections.step_never_taken', { step: stepOf(t, rejection.step) })
    case 'too_small_for_album':
      return t('checker.music.rejections.too_small_for_album', {
        minutes: formatNumber(numberOf(rejection.minutes), language),
        size: sizeText(t, numberOf(rejection.minimum_bytes), language),
      })
    case 'not_enough_seeders':
      return t('checker.music.rejections.not_enough_seeders', {
        seeders: formatNumber(numberOf(rejection.seeders), language),
        minimum: formatNumber(numberOf(rejection.minimum), language),
      })
    case 'older_than_retention':
      return t('checker.music.rejections.older_than_retention', {
        age: formatNumber(numberOf(rejection.age_days), language),
        retention: formatNumber(numberOf(rejection.retention_days), language),
      })
    case 'protocol_disabled':
      return rejection.protocol === 'torrent'
        ? t('checker.music.rejections.protocol_disabled_torrent')
        : t('checker.music.rejections.protocol_disabled_usenet')
    default:
      return t('checker.music.rejections.other', { code: rejection.code })
  }
}

export function albumNoteText(t: TFunction, note: AlbumNote, language = 'de'): string {
  switch (note.code) {
    case 'below_target':
      return t('checker.music.notes.below_target')
    case 'above_target':
      return t('checker.music.notes.above_target')
    case 'mp3_assumed':
      return t('checker.music.notes.mp3_assumed')
    case 'hires_first':
      return t('checker.music.notes.hires_first')
    case 'hires_last':
      return t('checker.music.notes.hires_last')
    case 'analogue_last':
      return t('checker.music.notes.analogue_last', { source: typeof note.source === 'string' ? note.source : '' })
    case 'cd_first':
      return t('checker.music.notes.cd_first')
    case 'repeat_first':
      return t('checker.music.notes.repeat_first')
    case 'tribute':
      return t('checker.music.notes.tribute')
    case 'karaoke':
      return t('checker.music.notes.karaoke')
    case 'format_from_category':
      return t('checker.music.notes.format_from_category')
    case 'category_differs':
      return t('checker.music.notes.category_differs')
    case 'small_for_step':
      return t('checker.music.notes.small_for_step', { step: stepOf(t, note.step), kbit: formatNumber(numberOf(note.kbit), language) })
    case 'large_for_album':
      return t('checker.music.notes.large_for_album', { minutes: formatNumber(numberOf(note.minutes), language) })
    case 'other_edition':
      return numberOf(note.media) > 0
        ? t('checker.music.notes.other_edition_media', { count: numberOf(note.media), value: formatNumber(numberOf(note.media), language) })
        : t('checker.music.notes.other_edition')
    default:
      return t('checker.music.notes.other', { code: note.code })
  }
}
