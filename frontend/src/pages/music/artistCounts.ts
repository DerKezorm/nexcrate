/**
 * Die Zeile unter dem Kuenstlernamen in der Listenansicht (Rueckmeldung 20.09.2026): Alben, was davon fehlt oder
 * besser gehen kann, und der Platz auf der Platte. Was null ist, steht nicht da. Dazu der eine Zustand, den die
 * Zeile als Abzeichen zeigt.
 */

import type { TFunction } from 'i18next'

import type { ArtistSummary, VersionState } from '../../api/types'
import { formatNumber } from '../../lib/format'
import { sizeText } from '../../lib/size'

/** Die Zustaende, die eine Zeile als Abzeichen zeigen kann, in derselben Reihenfolge wie der Server sie waehlt. */
const BADGE_STATES: readonly VersionState[] = ['problem', 'downloading', 'wanted', 'incomplete', 'upgrade']

export function badgeState(artist: ArtistSummary): VersionState | null {
  const state = artist.state
  return typeof state === 'string' && (BADGE_STATES as readonly string[]).includes(state) ? (state as VersionState) : null
}

export function stateLabel(t: TFunction, state: VersionState): string {
  switch (state) {
    case 'problem':
      return t('library.filter.problem')
    case 'downloading':
      return t('library.filter.downloading')
    case 'wanted':
      return t('library.filter.wanted')
    case 'incomplete':
      return t('library.filter.incomplete')
    default:
      return t('library.filter.upgrade')
  }
}

/** Die Teile der Zeile, schon als Text. Ohne Alben bleibt sie leer. */
export function countParts(t: TFunction, artist: ArtistSummary, language: string): string[] {
  const number = (value: number) => formatNumber(value, language)
  const parts = [t('music.artist.counts', { count: artist.albums, value: number(artist.albums), complete: number(artist.complete) })]
  const missing = (artist.missing ?? 0) + (artist.incomplete ?? 0)
  if (missing > 0) parts.push(t('music.artist.missing', { count: missing, value: number(missing) }))
  const upgradable = artist.upgrade ?? 0
  if (upgradable > 0) parts.push(t('music.artist.upgradable', { count: upgradable, value: number(upgradable) }))
  const size = artist.size_bytes ?? 0
  if (size > 0) parts.push(sizeText(t, size, language))
  return parts
}
