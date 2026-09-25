import type { TFunction } from 'i18next'

import type { MusicStep } from '../api/types'

const STEPS: readonly string[] = ['lossless_24', 'lossless', 'lossy_high', 'lossy_mid', 'lossy_low', 'unknown']

/** Die Qualitaetsstufen der Musik in Worten (Musik M2). Woertliche Schluessel, damit der Waechter sie findet. */
export function musicStepText(t: TFunction, step: MusicStep | 'unknown'): string {
  switch (step) {
    case 'lossless_24':
      return t('music.steps.lossless_24')
    case 'lossless':
      return t('music.steps.lossless')
    case 'lossy_high':
      return t('music.steps.lossy_high')
    case 'lossy_mid':
      return t('music.steps.lossy_mid')
    case 'lossy_low':
      return t('music.steps.lossy_low')
    default:
      return t('music.steps.unknown')
  }
}

/** Die Stufe aus `quality` einer Album-Fassung; was keine Stufe ist, bleibt, wie es dasteht. */
export function albumQualityText(t: TFunction, quality: string): string {
  return STEPS.includes(quality) ? musicStepText(t, quality as MusicStep | 'unknown') : quality
}
