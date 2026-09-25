import type { TFunction } from 'i18next'

import { formatDate, formatTime, isToday } from './format'

/**
 * Ein Zeitpunkt in Worten, in der Zeitzone des Browsers: "heute um 08:40" oder "am 16.09.2026 um 08:40". Ist er nicht
 * lesbar, steht der Text selbst da. Fuer Saetze wie "Nächste Suche heute um 08:40".
 */
export function whenText(t: TFunction, iso: string, language: string): string {
  if (Number.isNaN(new Date(iso).getTime())) return iso
  const time = formatTime(iso, language)
  return isToday(iso) ? t('common.when.today', { time }) : t('common.when.other', { date: formatDate(iso, language), time })
}
