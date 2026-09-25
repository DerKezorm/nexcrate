import { useTranslation } from 'react-i18next'

import { formatNumber } from '../../lib/format'

/** Mengen wie "1.284 Filme" in der Schreibweise der Sprache, Einzahl und Mehrzahl aus den Texten. */
export function useCounts() {
  const { t, i18n } = useTranslation()
  const value = (count: number) => formatNumber(count, i18n.language)
  return {
    movies: (count: number): string => t('import.count.movies', { count, value: value(count) }),
    files: (count: number): string => t('import.count.files', { count, value: value(count) }),
    // Seit S6: die Mengen einer Sonarr-Verbindung. Die Serien zaehlt derselbe Text wie im Import aus Sonarr.
    series: (count: number): string => t('series.import.series', { count, value: value(count) }),
    episodeFiles: (count: number): string => t('import.count.episodeFiles', { count, value: value(count) }),
    // Seit Musik M6: die Alben einer Lidarr-Verbindung.
    albums: (count: number): string => t('import.count.albums', { count, value: value(count) }),
  }
}
