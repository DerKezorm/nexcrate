import type { TFunction } from 'i18next'

import type { DelayRule, Protocol } from '../../api/types'
import { formatNumber } from '../../lib/format'

export function protocolName(t: TFunction, protocol: Protocol): string {
  return protocol === 'torrent' ? t('settings.delay.protocol.torrent') : t('settings.delay.protocol.usenet')
}

/** Minuten in Worten: unter zwei Stunden in Minuten, sonst in Stunden, ab zwei Tagen in Tagen, jeweils gerundet auf eine Stelle. */
export function minutesText(t: TFunction, minutes: number, language: string): string {
  if (minutes < 120) return t('settings.delay.minutes', { count: minutes, value: formatNumber(minutes, language) })
  if (minutes < 2880) {
    const hours = minutes / 60
    return t('settings.delay.hours', { count: hours, value: formatNumber(hours, language, Number.isInteger(hours) ? 0 : 1) })
  }
  const days = minutes / 1440
  return t('settings.delay.days', { count: days, value: formatNumber(days, language, Number.isInteger(days) ? 0 : 1) })
}

/** Die Regel in einem Satz, fuer die Kachel der Fassung. */
export function delayLineText(t: TFunction, rule: DelayRule, language: string): string {
  const parts: string[] = []
  if (!rule.enable_usenet) parts.push(t('settings.delay.line.only', { protocol: protocolName(t, 'torrent') }))
  else if (!rule.enable_torrent) parts.push(t('settings.delay.line.only', { protocol: protocolName(t, 'usenet') }))
  else parts.push(t('settings.delay.line.prefers', { protocol: protocolName(t, rule.preferred_protocol) }))

  const waits: string[] = []
  if (rule.enable_usenet && rule.usenet_minutes > 0)
    waits.push(t('settings.delay.line.waitsFor', { protocol: protocolName(t, 'usenet'), time: minutesText(t, rule.usenet_minutes, language) }))
  if (rule.enable_torrent && rule.torrent_minutes > 0)
    waits.push(t('settings.delay.line.waitsFor', { protocol: protocolName(t, 'torrent'), time: minutesText(t, rule.torrent_minutes, language) }))
  parts.push(waits.length > 0 ? t('settings.delay.line.waits', { list: waits.join(', ') }) : t('settings.delay.line.noWait'))
  return parts.join(' ')
}
