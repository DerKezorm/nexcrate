import type { TFunction } from 'i18next'

import type { CalendarKind, CalendarOccasion } from '../../api/calendar'
import type { SymbolName } from '../../components/Symbol'
import type { CalendarView } from './address'

/** Die Schluessel stehen woertlich da, damit keys.test.ts sie sieht. */
export function kindText(t: TFunction, kind: CalendarKind): string {
  switch (kind) {
    case 'movie':
      return t('calendar.kind.movie')
    case 'episode':
      return t('calendar.kind.episode')
    case 'album':
      return t('calendar.kind.album')
  }
}

export function occasionText(t: TFunction, occasion: CalendarOccasion): string {
  switch (occasion) {
    case 'theatrical':
      return t('calendar.occasion.theatrical')
    case 'digital':
      return t('calendar.occasion.digital')
    case 'physical':
      return t('calendar.occasion.physical')
    case 'air':
      return t('calendar.occasion.air')
    case 'release':
      return t('calendar.occasion.release')
  }
}

export function viewText(t: TFunction, view: CalendarView): string {
  return view === 'monat' ? t('calendar.view.month') : t('calendar.view.list')
}

export function kindSymbol(kind: CalendarKind): SymbolName {
  switch (kind) {
    case 'movie':
      return 'film'
    case 'episode':
      return 'tv'
    case 'album':
      return 'note'
  }
}

/** "S03E04". Fehlt eine der beiden Zahlen, steht nur die andere da. */
export function episodeNumber(season: number | null | undefined, episode: number | null | undefined): string {
  const parts: string[] = []
  if (typeof season === 'number') parts.push(`S${String(season).padStart(2, '0')}`)
  if (typeof episode === 'number') parts.push(`E${String(episode).padStart(2, '0')}`)
  return parts.join('')
}

/** Der Name eines Landes in der Sprache der Oberflaeche, mit dem Kuerzel als Rueckfall. */
export function countryName(country: string, language: string): string {
  try {
    const names = new Intl.DisplayNames([language], { type: 'region' })
    return names.of(country) ?? country
  } catch {
    return country
  }
}
