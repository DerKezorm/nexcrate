import { useTranslation } from 'react-i18next'

import type { SymbolName } from '../../components/Symbol'

/** Die drei Bereiche der Oberflaeche. Musik heisst hier `music`, am Server `album`. */
export type KindSection = 'movie' | 'series' | 'music'

/** Name einer Medienart in der Mehrzahl, etwa "Filme". */
export function useKindLabel(): (kind: KindSection) => string {
  const { t } = useTranslation()
  return (kind) => (kind === 'movie' ? t('common.kind.movies') : kind === 'series' ? t('common.kind.seriesPlural') : t('common.kind.musicPlural'))
}

export const KIND_SYMBOL: Record<KindSection, SymbolName> = { movie: 'film', series: 'tv', music: 'note' }
