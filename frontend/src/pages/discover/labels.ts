import { useTranslation } from 'react-i18next'

/** Die Namen und Erklaerungen der Listen, jeder Schluessel woertlich (`keys.test.ts`). */
export function useListLabels() {
  const { t } = useTranslation()
  return {
    movie: {
      fresh: t('discover.lists.movie.fresh'),
      popular: t('discover.lists.movie.popular'),
      acclaimed: t('discover.lists.movie.acclaimed'),
      classics: t('discover.lists.movie.classics'),
    } as Record<string, string>,
    movieHint: {
      fresh: t('discover.hints.movie.fresh'),
      popular: t('discover.hints.movie.popular'),
      acclaimed: t('discover.hints.movie.acclaimed'),
      classics: t('discover.hints.movie.classics'),
    } as Record<string, string>,
    series: {
      new: t('discover.lists.series.new'),
      popular: t('discover.lists.series.popular'),
      ended: t('discover.lists.series.ended'),
      classics: t('discover.lists.series.classics'),
    } as Record<string, string>,
    seriesHint: {
      new: t('discover.hints.series.new'),
      popular: t('discover.hints.series.popular'),
      ended: t('discover.hints.series.ended'),
      classics: t('discover.hints.series.classics'),
    } as Record<string, string>,
    album: {
      fresh: t('discover.lists.album.fresh'),
      trending: t('discover.lists.album.trending'),
      classics: t('discover.lists.album.classics'),
    } as Record<string, string>,
    albumHint: {
      fresh: t('discover.hints.album.fresh'),
      trending: t('discover.hints.album.trending'),
      classics: t('discover.hints.album.classics'),
    } as Record<string, string>,
  }
}
