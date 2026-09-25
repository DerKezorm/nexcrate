import type { TFunction } from 'i18next'

import type { AlbumMatch, AlbumSearchRelease } from '../../api/types'
import { formatNumber } from '../../lib/format'
import { albumRejectionText } from '../checker/musicCheckerText'

/** Seeder bei Torrents, sonst Grabs; null, wenn der Indexer nichts sagt. */
export function peersOf(t: TFunction, release: AlbumSearchRelease, language: string): string | null {
  if (release.protocol === 'torrent') {
    return release.seeders === null ? null : t('search.releases.seeders', { count: release.seeders, value: formatNumber(release.seeders, language) })
  }
  return release.grabs === null ? null : t('search.releases.grabs', { count: release.grabs, value: formatNumber(release.grabs, language) })
}

/** Die Art, die der Name nennt, in Worten. Woertliche Schluessel fuer den Waechter; Unbekanntes zeigt die Kennung. */
export function kindText(t: TFunction, kind: string): string {
  switch (kind) {
    case 'single':
      return t('search.album.kinds.single')
    case 'ep':
      return t('search.album.kinds.ep')
    case 'live':
      return t('search.album.kinds.live')
    case 'soundtrack':
      return t('search.album.kinds.soundtrack')
    case 'compilation':
      return t('search.album.kinds.compilation')
    case 'demo':
      return t('search.album.kinds.demo')
    case 'mixtape':
      return t('search.album.kinds.mixtape')
    default:
      return kind
  }
}

/** Warum ein Release nicht zu diesem Album gehoert, in einem Satz. */
export function matchText(t: TFunction, match: AlbumMatch): string {
  switch (match.kind) {
    case 'unknown_album':
      return match.read_album
        ? t('search.album.match.unknownAlbum', { album: match.read_album })
        : t('search.album.match.unknownAlbumNoName')
    case 'other_artist':
      return match.read_artist ? t('search.album.match.otherArtist', { artist: match.read_artist }) : t('search.album.match.otherArtistNoName')
    case 'not_an_album':
      switch (match.reason) {
        case 'several_albums':
          return t('search.album.match.several')
        case 'tribute':
          return t('search.album.match.tribute')
        case 'karaoke':
          return t('search.album.match.karaoke')
        case 'sampler':
          return t('search.album.match.sampler')
        case 'not_a_sampler':
          return t('search.album.match.notSampler')
        default:
          return t('search.album.match.other', { code: match.reason ?? match.kind })
      }
    default:
      return t('search.album.match.other', { code: match.kind })
  }
}

/** Ein haeufiger Grund der Karte "Nichts passt" mit der Zahl der Releases. */
export function albumReasonCountText(t: TFunction, reason: { code: string; count: number }, language: string): string {
  return t('search.album.reasonCount', {
    count: reason.count,
    value: formatNumber(reason.count, language),
    reason: albumRejectionText(t, { code: reason.code }, language),
  })
}
