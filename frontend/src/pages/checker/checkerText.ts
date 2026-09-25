/**
 * Die Saetze zum Release-Pruefer. Der Server schickt Ablehnungen und Gruende als Codes mit Werten,
 * nie als Saetze; hier werden sie zu Text, mit Groessen und Punkten in der Schreibweise der Sprache.
 */

import type { TFunction } from 'i18next'

import type { ReleaseRejection, ReleaseResult, ReleaseUpgrade } from '../../api/types'
import { formatList, formatNumber } from '../../lib/format'
import { languageText } from '../../lib/names'
import { sizeText } from '../../lib/size'
import { languageName, listValues } from '../profiles/profileText'

/** 1024 wie im Server und in Radarr. */
export const GIB = 1024 ** 3

/**
 * Die Ablehnungen der Bewertung. `not_enough_seeders` kommt seit 2c aus der Suche dazu, vom Indexer und nicht vom Profil;
 * `protocol_disabled` seit den Verzoegerungsregeln, von der Fassung.
 */
export const REJECTION_CODES = [
  'quality_not_allowed',
  'score_below_minimum',
  'too_small',
  'too_large',
  'language_missing',
  'hardcoded_subs',
  'unknown_quality',
  'not_enough_seeders',
  'older_than_retention',
  'protocol_disabled',
] as const

export const UPGRADE_REASONS = ['worse_quality', 'upgrades_disabled', 'cutoff_met', 'score_not_higher', 'upgrade_until_reached', 'step_too_small'] as const

/** Wie ein Ergebnis dasteht: passt, passt vorerst oder passt nicht. */
export type FitState = 'fits' | 'forNow' | 'fitsNot'

/**
 * "Vorerst" heisst: Das Release passt, liegt aber unter der Zielaufloesung des Profils und wird ersetzt,
 * sobald eines in der Zielaufloesung kommt. Nur ein Release, das passt, kann "vorerst" sein. Fehlt
 * `below_target` (ein Server von davor), gilt es als nicht darunter.
 */
export function fitState(result: ReleaseResult): FitState {
  if (!result.accepted) return 'fitsNot'
  return result.below_target === true ? 'forNow' : 'fits'
}

/** Eine Groesse in GB, mit Komma oder Punkt. Null, wenn es keine positive Zahl ist. */
export function parseGb(text: string): number | null {
  const clean = text.trim().replace(',', '.')
  if (!/^\d{1,6}(\.\d{1,3})?$/.test(clean)) return null
  const value = Number(clean)
  return value > 0 ? value : null
}

export function gbToBytes(gb: number): number {
  return Math.round(gb * GIB)
}

function numberOf(value: unknown): number {
  const number = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(number) ? number : 0
}

/** Eine Sprache aus dem Server: ein Kuerzel wie "de" oder ein Name wie Radarr ihn fuehrt ("German"). */
export function releaseLanguage(t: TFunction, value: string): string {
  return /^[a-z]{2}$/.test(value) ? languageName(t, value) : languageText(t, value)
}

export function rejectionText(t: TFunction, rejection: ReleaseRejection, language: string): string {
  switch (rejection.code) {
    case 'quality_not_allowed':
      return t('checker.rejections.quality_not_allowed', { quality: listValues(rejection.quality).join(', ') })
    case 'score_below_minimum':
      return t('checker.rejections.score_below_minimum', {
        score: formatNumber(numberOf(rejection.score), language),
        minimum: formatNumber(numberOf(rejection.minimum), language),
      })
    case 'too_small':
      return t('checker.rejections.too_small', {
        size: sizeText(t, numberOf(rejection.size_bytes), language),
        minimum: sizeText(t, numberOf(rejection.minimum_bytes), language),
      })
    case 'too_large':
      return t('checker.rejections.too_large', {
        size: sizeText(t, numberOf(rejection.size_bytes), language),
        maximum: sizeText(t, numberOf(rejection.maximum_bytes), language),
      })
    case 'language_missing':
      return t('checker.rejections.language_missing', {
        languages: formatList(
          listValues(rejection.languages).map((value) => releaseLanguage(t, value)),
          language,
        ),
      })
    case 'hardcoded_subs': {
      const value = listValues(rejection.value).join(', ')
      return value !== '' ? t('checker.rejections.hardcoded_subs', { value }) : t('checker.rejections.hardcoded_subsPlain')
    }
    case 'unknown_quality':
      return t('checker.rejections.unknown_quality')
    case 'not_enough_seeders': {
      const seeders = numberOf(rejection.seeders)
      return t('checker.rejections.not_enough_seeders', {
        count: seeders,
        seeders: formatNumber(seeders, language),
        minimum: formatNumber(numberOf(rejection.minimum), language),
      })
    }
    case 'older_than_retention':
      return t('checker.rejections.older_than_retention', {
        age: formatNumber(numberOf(rejection.age_days), language),
        retention: formatNumber(numberOf(rejection.retention_days), language),
      })
    case 'protocol_disabled':
      return rejection.protocol === 'torrent' ? t('checker.rejections.protocol_disabled_torrent') : t('checker.rejections.protocol_disabled_usenet')
    default:
      return t('checker.rejections.unknown', { code: rejection.code })
  }
}

function reasonText(t: TFunction, reason: string | null): string | null {
  switch (reason) {
    case null:
      return null
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
      return t('checker.upgrade.reasons.unknown', { reason })
  }
}

/** Ob das Release besser waere als die vorhandene Datei, warum nicht, und was vorhanden ist. */
export function upgradeText(t: TFunction, upgrade: ReleaseUpgrade, language: string): { headline: string; reason: string | null; current: string } {
  const score = formatNumber(upgrade.current_score, language)
  return {
    headline: upgrade.better ? t('checker.upgrade.better') : t('checker.upgrade.notBetter'),
    reason: upgrade.better ? null : reasonText(t, upgrade.reason),
    current: upgrade.current_quality ? t('checker.upgrade.current', { quality: upgrade.current_quality, score }) : t('checker.upgrade.currentNoQuality', { score }),
  }
}
