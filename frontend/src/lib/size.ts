import type { TFunction } from 'i18next'

import { formatGb, formatNumber } from './format'

const KIB = 1024
const MIB = 1024 ** 2
const GIB = 1024 ** 3

/**
 * Bytes in der passenden Einheit, gerechnet mit 1024 wie in Radarr. So stehen dieselben
 * Zahlen da, die man aus Radarr kennt. Unter einem GB in MB, unter einem MB in KB: Eine kleine
 * Bibliothek am Anfang soll nicht "0,0 GB" zeigen.
 */
export function sizeText(t: TFunction, bytes: number, language: string): string {
  const value = Math.max(0, bytes)
  if (value >= 1024 * GIB) return t('common.units.tb', { value: formatNumber(value / GIB / 1024, language, 1) })
  if (value >= GIB) return t('common.units.gb', { value: formatGb(value / GIB, language) })
  if (value >= MIB) return t('common.units.mb', { value: formatNumber(value / MIB, language, 0) })
  return t('common.units.kb', { value: formatNumber(Math.ceil(value / KIB), language, 0) })
}
