/**
 * Die Saetze zum Laden aus der Suche. Die Gruende kommen als Codes aus "Which versions can load" im Plan.
 * Die Schluessel stehen woertlich da, damit `keys.test.ts` sie sieht.
 */

import type { TFunction } from 'i18next'

import type { LoadConfirmation, SearchRelease, SearchReleaseVersion } from '../../api/types'
import { DOWNLOADS_PATH } from '../downloads/address'
import { CLIENTS_TAB_PATH, FILES_TAB_PATH, VERSIONS_TAB_PATH } from '../settings/tabs'
import { isTorrent } from './searchText'

/** Warum Laden nicht geht, in einem Satz. `code` ist leer, wenn der Server keinen Grund nennt. */
export function loadBlockText(t: TFunction, code: string, label: string, release: SearchRelease): string {
  switch (code) {
    case 'version_fed_by_source':
      return t('search.load.block.version_fed_by_source', { label })
    case 'version_no_profile':
      return t('search.load.block.version_no_profile', { label })
    case 'version_no_folder':
      return t('search.load.block.version_no_folder', { label })
    case 'no_client_for_protocol':
      return isTorrent(release) ? t('search.load.block.noClientTorrent') : t('search.load.block.noClientUsenet')
    case 'download_active':
      return t('search.load.block.download_active', { label })
    case 'episodes_downloading':
      return t('search.load.block.episodes_downloading', { label })
    case '':
      return t('search.load.block.unknownNoCode')
    default:
      return t('search.load.block.unknown', { code })
  }
}

/** Wohin es von dort weitergeht, oder null, wenn es keinen Weg gibt. */
export function loadBlockLink(t: TFunction, code: string): { to: string; text: string } | null {
  switch (code) {
    case 'version_no_profile':
      return { to: VERSIONS_TAB_PATH, text: t('search.load.blockLink.versions') }
    case 'version_no_folder':
      return { to: FILES_TAB_PATH, text: t('search.load.blockLink.folders') }
    case 'no_client_for_protocol':
      return { to: CLIENTS_TAB_PATH, text: t('search.load.blockLink.clients') }
    case 'download_active':
    case 'episodes_downloading':
      return { to: DOWNLOADS_PATH, text: t('search.load.blockLink.downloads') }
    default:
      return null
  }
}

/** Was vor dem Laden bestaetigt werden muss: ein Release, das nicht passt, oder eines auf der Sperrliste. */
export function confirmationsFor(release: SearchRelease, entry: SearchReleaseVersion): LoadConfirmation[] {
  const needs: LoadConfirmation[] = []
  if (entry.result && !entry.result.accepted) needs.push('not_fitting')
  const series = entry.series_result
  if (series && !series.accepted) needs.push('not_fitting')
  if (release.blocklisted === true) needs.push('blocklisted')
  // Seit S4: ein Serien-Release ohne Gewinn laedt nur nach Bestaetigung (Entscheidung 4).
  if (series && !series.episodes.some((episode) => episode.state === 'fills' || episode.state === 'replaces')) needs.push('no_gain')
  return needs
}

/** Wie viele vorhandene Dateien ein Serien-Release ohne Gewinn ersetzen wuerde: die Folgen, die ihre Datei behalten. */
export function keepsOf(entry: SearchReleaseVersion): number {
  return (entry.series_result?.episodes ?? []).filter((episode) => episode.state === 'keeps').length
}
