import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ratingsApi, type TitleRatings } from '../../api/outside'
import { formatNumber } from '../../lib/format'

/**
 * Die Wertungen eines Titels (V4): IMDb aus nexcrates Kopie der taeglichen Datei, mit einem
 * OMDb-Schluessel dazu Rotten Tomatoes und Metacritic. Fehlt ein Wert, fehlt seine Zeile; ohne jeden Wert steht nichts.
 */
export function RatingBadges({ titleId }: { titleId: number }) {
  const { t, i18n } = useTranslation()
  const [ratings, setRatings] = useState<TitleRatings | null>(null)

  useEffect(() => {
    let current = true
    ratingsApi.ofTitle(titleId).then(
      (found) => current && setRatings(found),
      () => undefined,
    )
    return () => {
      current = false
    }
  }, [titleId])

  // Nur eine Antwort in der erwarteten Form zaehlt; alles andere zeigt keine Wertung.
  if (ratings === null || typeof ratings !== 'object' || typeof ratings.sources !== 'object') return null
  const parts = [
    ratings.imdb !== null
      ? t('title.ratings.imdb', {
          rating: formatNumber(ratings.imdb.rating, i18n.language, 1),
          votes: formatNumber(ratings.imdb.votes, i18n.language),
        })
      : null,
    ratings.rotten_tomatoes !== null ? t('title.ratings.rotten', { value: ratings.rotten_tomatoes }) : null,
    ratings.metacritic !== null ? t('title.ratings.metacritic', { value: ratings.metacritic }) : null,
  ].filter((part): part is string => part !== null)
  if (parts.length === 0) return null
  return (
    <ul className="flex flex-wrap gap-2 text-xs" aria-label={t('title.ratings.label')}>
      {parts.map((part) => (
        <li key={part} className="rounded-full border border-ink-600 bg-ink-900 px-2.5 py-1 text-mist-300 tabular-nums">
          {part}
        </li>
      ))}
    </ul>
  )
}
