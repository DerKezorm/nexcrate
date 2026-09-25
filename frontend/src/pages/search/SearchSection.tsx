import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { Search } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage, Section, Spinner, Switch } from '../../components/ui'
import { formatNumber, formatTime } from '../../lib/format'
import { IndexerProgress, SkippedLine } from './IndexerProgress'
import { OtherMovies } from './OtherMovies'
import { SearchLoadingContext, type LoadedRelease, type SearchLoading } from './searchLoading'
import { fits, otherMovies, rowsForVersion } from './searchOrder'
import { failureText, isFailed, isSkipped, pollProblemText, startProblemText } from './searchText'
import { SeriesOutcome } from './SeriesOutcome'
import { isSeriesSearch, scopeText } from './seriesSearchText'
import type { TitleSearch } from './useTitleSearch'
import { VersionOutcome } from './VersionOutcome'
import { VersionReleases } from './VersionReleases'

/**
 * Die Suche auf der Titelseite eines Films. Waehrend sie laeuft: je Indexer Zustand und Anfragen.
 * Danach je Fassung eine Karte mit dem Ergebnis und darunter ihre Releases. Die Suche selbst laedt
 * nichts; seit Schritt 3 laedt "Laden" ein Release, und `onLoaded` sagt der Seite danach Bescheid.
 *
 * Seit S3 auch fuer Serien (`series`): Der Kopf nennt den Umfang, die Karte je Fassung zeigt die Zusammenstellung,
 * uebersprungene Indexer stehen mit Grund da. Laden gibt es bei Serien noch nicht.
 */
export function SearchSection({
  search,
  starting,
  problem,
  onLoaded,
  series = false,
}: Pick<TitleSearch, 'search' | 'starting' | 'problem'> & { onLoaded?: () => void; series?: boolean }) {
  const { t } = useTranslation()
  const scope = search !== null && isSeriesSearch(search) ? scopeText(t, search) : null

  return (
    <Section title={t('search.title')} intro={t('search.intro')}>
      {scope && <p className="text-sm font-semibold text-mist-200">{scope}</p>}
      {problem && <FormMessage>{problem.stage === 'start' ? startProblemText(t, problem.error, series) : pollProblemText(t, problem.error)}</FormMessage>}
      {search === null ? (
        starting &&
        problem === null && (
          <p role="status" className="flex items-center gap-2 text-sm text-mist-400">
            <Spinner />
            {t('search.starting')}
          </p>
        )
      ) : search.state === 'done' ? (
        <SearchResults key={search.search_id} search={search} onLoaded={onLoaded} />
      ) : (
        <div className="flex flex-col gap-3">
          {problem === null && (
            <p role="status" className="flex items-center gap-2 text-sm text-mist-300">
              <Spinner />
              {t('search.running')}
            </p>
          )}
          <IndexerProgress indexers={search.indexers} />
        </div>
      )}
    </Section>
  )
}

function SearchResults({ search, onLoaded }: { search: Search; onLoaded?: () => void }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const [onlyFitting, setOnlyFitting] = useState(false)
  const [queries, setQueries] = useState(false)
  // Was in dieser Suche geladen wurde, je Fassung. Eine neue Suche beginnt leer (`key` oben).
  const [loaded, setLoaded] = useState<ReadonlyMap<number, LoadedRelease>>(() => new Map())
  const [loadedKeys, setLoadedKeys] = useState<ReadonlySet<string>>(() => new Set())
  const loading = useMemo<SearchLoading>(
    () => ({
      searchId: search.search_id,
      versions: search.versions,
      loaded,
      loadedKeys,
      onLoaded: (versionId, releaseKey, download) => {
        setLoaded((current) => new Map(current).set(versionId, { releaseKey, download }))
        setLoadedKeys((current) => new Set(current).add(`${versionId}:${releaseKey}`))
        onLoaded?.()
      },
    }),
    [search.search_id, search.versions, loaded, loadedKeys, onLoaded],
  )
  const series = isSeriesSearch(search)
  const failed = search.indexers.filter(isFailed)
  const skipped = search.indexers.filter(isSkipped)
  const others = otherMovies(search.releases)
  const belonging = search.releases.length - others.length
  const summary = t('search.summary', {
    indexers: t('search.summaryIndexers', { count: search.indexers.length, value: formatNumber(search.indexers.length, language) }),
    releases: series
      ? t('search.series.summaryReleases', { count: belonging, value: formatNumber(belonging, language) })
      : t('search.summaryReleases', { count: belonging, value: formatNumber(belonging, language) }),
  })

  return (
    <SearchLoadingContext.Provider value={loading}>
      <div className="flex flex-col gap-5">
        <div className="flex flex-col gap-0.5">
          <p className="text-sm text-mist-200">{summary}</p>
          {search.finished_at && <p className="text-xs text-mist-500">{t('search.doneAt', { time: formatTime(search.finished_at, language) })}</p>}
        </div>

        {failed.length > 0 && (
          <div className="flex flex-col gap-2 rounded-xl border border-bad-500/40 bg-bad-500/10 p-3">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-bad-500">
              <Symbol name="alert" className="h-4 w-4 shrink-0" />
              {t('search.indexers.failedTitle')}
            </h3>
            <ul aria-label={t('search.indexers.failedTitle')} className="flex flex-col gap-1.5 pl-6">
              {failed.map((indexer) => (
                <li key={indexer.indexer_id} className="flex min-w-0 flex-col text-sm">
                  <span className="font-semibold wrap-anywhere text-mist-100">{indexer.name}</span>
                  <span className="wrap-anywhere text-mist-300">{failureText(t, indexer)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {skipped.length > 0 && (
          <div className="flex flex-col gap-2 rounded-xl border border-info-500/30 bg-info-500/5 p-3">
            <h3 className="text-sm font-semibold text-mist-200">{t('search.indexers.skippedTitle')}</h3>
            <ul aria-label={t('search.indexers.skippedTitle')} className="flex flex-col gap-1.5">
              {skipped.map((indexer) => (
                <li key={indexer.indexer_id} className="flex min-w-0 flex-col text-sm">
                  <span className="font-semibold wrap-anywhere text-mist-100">{indexer.name}</span>
                  <SkippedLine indexer={indexer} />
                </li>
              ))}
            </ul>
          </div>
        )}

        <Switch label={t('search.releases.onlyFitting')} hint={t('search.releases.onlyFittingHint')} checked={onlyFitting} onChange={setOnlyFitting} />

        {search.versions.map((version) => {
          const rows = rowsForVersion(search.releases, version.version_id)
          return (
            <section key={version.version_id} aria-label={version.label} className="flex flex-col gap-3 border-t border-ink-700 pt-5">
              {series ? <SeriesOutcome version={version} rows={rows} releases={search.releases} /> : <VersionOutcome version={version} rows={rows} />}
              {version.has_profile && (
                <VersionReleases
                  label={version.label}
                  rows={onlyFitting ? rows.filter((row) => fits(row.entry)) : rows}
                  onlyFitting={onlyFitting}
                  series={series}
                  takenKey={version.would_take}
                />
              )}
            </section>
          )
        })}

        {others.length > 0 && (
          <div className="border-t border-ink-700 pt-5">
            <OtherMovies releases={others} series={series} />
          </div>
        )}

        <div className="flex flex-col gap-3 border-t border-ink-700 pt-5">
          <div>
            <Button variant="ghost" size="sm" aria-expanded={queries} onClick={() => setQueries((current) => !current)}>
              <Symbol name={queries ? 'chevronDown' : 'chevron'} className="h-3.5 w-3.5" />
              {queries ? t('search.indexers.hideQueries') : t('search.indexers.showQueries')}
            </Button>
          </div>
          {queries && <IndexerProgress indexers={search.indexers} />}
        </div>
      </div>
    </SearchLoadingContext.Provider>
  )
}
