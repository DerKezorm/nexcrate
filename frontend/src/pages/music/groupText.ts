import type { TFunction } from 'i18next'

import type { AlbumGroupName } from '../../api/types'

/** Die Gruppen einer Kuenstlerseite in ihrer Reihenfolge (Entscheidung 26, `services/music/kinds.py`). */
export const ALBUM_GROUPS: readonly AlbumGroupName[] = ['studio', 'ep', 'single', 'live', 'compilation', 'soundtrack', 'remix', 'spoken', 'other']

/** Der Name einer Gruppe, etwa "Studioalben" oder "Singles". Eine unbekannte Gruppe (ein neuerer Server) faellt unter "Sonstige". */
export function groupLabel(t: TFunction, group: string): string {
  switch (group) {
    case 'studio':
      return t('music.group.studio')
    case 'ep':
      return t('music.group.ep')
    case 'single':
      return t('music.group.single')
    case 'live':
      return t('music.group.live')
    case 'compilation':
      return t('music.group.compilation')
    case 'soundtrack':
      return t('music.group.soundtrack')
    case 'remix':
      return t('music.group.remix')
    case 'spoken':
      return t('music.group.spoken')
    default:
      return t('music.group.other')
  }
}
