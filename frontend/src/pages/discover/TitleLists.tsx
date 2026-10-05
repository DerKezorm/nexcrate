import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, errorText } from '../../api/client'
import { DISCOVER_LISTS, discoverApi, type DiscoverFilters } from '../../api/discover'
import type { DiscoverGenre, DiscoverTitle, DiscoverTitles, TmdbState } from '../../api/types'
import { Segmented } from '../../components/Segmented'
import { FormMessage, Spinner } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatNumber } from '../../lib/format'
import { AddDialog } from '../library/AddDialog'
import { TmdbTokenForm } from '../settings/TmdbTokenForm'
import { DISCOVER_GRID, DiscoverCard } from './DiscoverCard'
import { useListLabels } from './labels'
import { COUNTRIES, LANGUAGES, named } from './places'

type Kind = 'movie' | 'series'

const selectClass =
  'min-w-0 rounded-full border border-ink-700 bg-ink-900 px-3.5 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none'

/**
 * Filme oder Serien in Entdecken: Knoepfe fuer die Liste, Filter darunter, dann hoechstens 20 Karten. Was man
 * hinzufuegt oder ausblendet, verschwindet sofort; danach holt die Seite die Liste neu, damit wieder 20 dastehen
 * (die Seiten von TMDB liegen beim Server sechs Stunden, das kostet keine neue Anfrage dort).
 */
export function TitleLists({
  kind,
  list,
  filters,
  tmdbConfigured,
  preferredRegion = null,
  onList,
  onFilters,
  onTmdbSaved,
  onHidden,
}: {
  kind: Kind
  list: string
  filters: DiscoverFilters
  tmdbConfigured: boolean
  /** Das Land der Kontosprache: steht in der Auswahl oben. */
  preferredRegion?: string | null
  onList: (list: string) => void
  onFilters: (filters: DiscoverFilters) => void
  onTmdbSaved: (state: TmdbState) => void
  onHidden: () => void
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const notify = useNotice()
  const labels = useListLabels()
  const lists = DISCOVER_LISTS[kind] as readonly string[]

  const [found, setFound] = useState<DiscoverTitles | null>(null)
  const [loading, setLoading] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const [genres, setGenres] = useState<DiscoverGenre[] | null>(null)
  const [opened, setOpened] = useState<DiscoverTitle | null>(null)
  // Titel, die gerade hinzugefuegt oder ausgeblendet wurden: bis die neue Liste da ist, nicht mehr zeigen.
  const [gone, setGone] = useState<ReadonlySet<number>>(new Set())
  const [round, setRound] = useState(0)
  const generation = useRef(0)

  useEffect(() => {
    if (!tmdbConfigured) return
    let current = true
    setGenres(null)
    discoverApi.genres(kind).then(
      (result) => current && setGenres(result),
      () => current && setGenres([]),
    )
    return () => {
      current = false
    }
  }, [kind, tmdbConfigured])

  useEffect(() => {
    if (!tmdbConfigured) return
    const run = ++generation.current
    const abort = new AbortController()
    setLoading(true)
    setProblem(null)
    discoverApi.titles(kind, list, filters, abort.signal).then(
      (result) => {
        if (run !== generation.current) return
        setFound(result)
        setGone(new Set())
        setLoading(false)
      },
      (error: unknown) => {
        if (run !== generation.current || abort.signal.aborted) return
        setLoading(false)
        setProblem(error)
      },
    )
    return () => abort.abort()
  }, [kind, list, filters, tmdbConfigured, round])

  const countries = useMemo(() => {
    const all = named(COUNTRIES, language, 'region')
    const first = all.filter((entry) => entry.code === preferredRegion)
    return [...first, ...all.filter((entry) => entry.code !== preferredRegion)]
  }, [language, preferredRegion])
  const languages = useMemo(() => named(LANGUAGES, language, 'language'), [language])

  if (!tmdbConfigured || (problem instanceof ApiError && problem.code === 'tmdb_not_configured')) {
    return (
      <div className="flex max-w-xl flex-col gap-4">
        <p className="text-sm text-mist-300">{t('discover.tmdbNeeded')}</p>
        <TmdbTokenForm configured={false} onSaved={onTmdbSaved} />
      </div>
    )
  }

  function leave(item: DiscoverTitle) {
    setGone((current) => new Set(current).add(item.tmdb_id))
    setRound((value) => value + 1)
  }

  async function hide(item: DiscoverTitle) {
    setGone((current) => new Set(current).add(item.tmdb_id))
    try {
      await discoverApi.hide(kind, String(item.tmdb_id))
      onHidden()
      setRound((value) => value + 1)
    } catch (error) {
      setGone((current) => {
        const next = new Set(current)
        next.delete(item.tmdb_id)
        return next
      })
      notify(errorText(t, error))
    }
  }

  const listLabel = (value: string) => (kind === 'series' ? labels.series : labels.movie)[value] ?? value
  const listHint = (kind === 'series' ? labels.seriesHint : labels.movieHint)[list] ?? ''
  const items = (found?.items ?? []).filter((item) => !gone.has(item.tmdb_id))
  const regionOff = kind === 'movie' && list === 'classics'

  const metaOf = (item: DiscoverTitle) =>
    [
      item.year !== null && item.year > 0 ? String(item.year) : null,
      item.rating !== null ? t('discover.card.rating', { value: formatNumber(item.rating, language, 1) }) : null,
    ]
      .filter(Boolean)
      .join(' · ')

  return (
    <div className="flex flex-col gap-5">
      <div className="overflow-x-auto">
        <Segmented value={list} options={lists} onChange={onList} label={listLabel} ariaLabel={t('discover.listLabel')} />
      </div>
      <p className="-mt-2 text-sm text-mist-500">{listHint}</p>

      <div className="flex flex-wrap gap-2" role="group" aria-label={t('discover.filters.label')}>
        {kind === 'movie' && (
          <select
            className={selectClass}
            value={regionOff ? '' : filters.region}
            disabled={regionOff}
            onChange={(event) => onFilters({ ...filters, region: event.target.value })}
            aria-label={t('discover.filters.region')}
            title={regionOff ? t('discover.filters.regionOff') : undefined}
          >
            <option value="">{t('discover.filters.anyRegion')}</option>
            {countries.map((entry) => (
              <option key={entry.code} value={entry.code}>
                {entry.name}
              </option>
            ))}
          </select>
        )}
        <select
          className={selectClass}
          value={filters.genre}
          onChange={(event) => onFilters({ ...filters, genre: event.target.value })}
          aria-label={t('discover.filters.genre')}
        >
          <option value="">{t('discover.filters.anyGenre')}</option>
          {(genres ?? []).map((genre) => (
            <option key={genre.id} value={String(genre.id)}>
              {genre.name}
            </option>
          ))}
        </select>
        <select
          className={selectClass}
          value={filters.language}
          onChange={(event) => onFilters({ ...filters, language: event.target.value })}
          aria-label={t('discover.filters.language')}
        >
          <option value="">{t('discover.filters.anyLanguage')}</option>
          {languages.map((entry) => (
            <option key={entry.code} value={entry.code}>
              {entry.name}
            </option>
          ))}
        </select>
        {kind === 'series' && (
          <select
            className={selectClass}
            value={filters.country}
            onChange={(event) => onFilters({ ...filters, country: event.target.value })}
            aria-label={t('discover.filters.country')}
          >
            <option value="">{t('discover.filters.anyCountry')}</option>
            {countries.map((entry) => (
              <option key={entry.code} value={entry.code}>
                {entry.name}
              </option>
            ))}
          </select>
        )}
      </div>

      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      {found === null ? (
        problem === null && (
          <p className="flex items-center justify-center gap-2 py-16 text-sm text-mist-500" role="status">
            <Spinner />
            {t('discover.loading')}
          </p>
        )
      ) : items.length === 0 && !loading ? (
        <p className="rounded-2xl border border-dashed border-ink-700 px-6 py-12 text-center text-sm text-mist-500" role="status">
          {t('discover.empty')}
        </p>
      ) : (
        <>
          <ul className={DISCOVER_GRID + ' transition-opacity ' + (loading ? 'opacity-60' : '')} aria-busy={loading}>
            {items.map((item) => (
              <DiscoverCard
                key={item.tmdb_id}
                title={item.title}
                meta={metaOf(item)}
                posterUrl={item.poster_url}
                onOpen={() => setOpened(item)}
                onHide={() => void hide(item)}
              />
            ))}
          </ul>
          {found.exhausted && items.length < 20 && <p className="text-xs text-mist-500">{t('discover.noMore')}</p>}
        </>
      )}

      {opened !== null && (
        <AddDialog
          kind={kind}
          initial={opened}
          onClose={() => setOpened(null)}
          onAdded={() => {
            notify(t('discover.added', { title: opened.title }))
            leave(opened)
          }}
        />
      )}
    </div>
  )
}
