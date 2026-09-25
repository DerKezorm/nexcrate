import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { SearchIndexer } from '../../api/types'
import { Symbol, type SymbolName } from '../../components/Symbol'
import { Badge, Spinner } from '../../components/ui'
import { Tile, TileHeader } from '../settings/parts'
import { INDEXERS_TAB_PATH } from '../settings/tabs'
import { failureText, indexerStateText, isFailed, isSkipped, queryText, releaseCountText, skippedText } from './searchText'

const LOOK: Record<string, { tone: 'neutral' | 'info' | 'ok' | 'bad'; symbol: SymbolName | null }> = {
  waiting: { tone: 'neutral', symbol: 'clock' },
  searching: { tone: 'info', symbol: null },
  done: { tone: 'ok', symbol: 'check' },
  failed: { tone: 'bad', symbol: 'alert' },
  timeout: { tone: 'bad', symbol: 'clock' },
  skipped: { tone: 'neutral', symbol: 'info' },
}

function StateBadge({ state }: { state: string }) {
  const { t } = useTranslation()
  const look = LOOK[state] ?? { tone: 'neutral', symbol: 'info' }
  return (
    <Badge tone={look.tone}>
      {look.symbol === null ? <Spinner className="h-3.5 w-3.5" /> : <Symbol name={look.symbol} className="h-3.5 w-3.5" />}
      {indexerStateText(t, state)}
    </Badge>
  )
}

/** Seit S3: warum ein Indexer uebersprungen wurde, mit dem Weg zu seinen Kategorien. */
export function SkippedLine({ indexer }: { indexer: SearchIndexer }) {
  const { t } = useTranslation()
  return (
    <p className="flex flex-wrap items-start gap-x-2 gap-y-1 text-sm text-mist-300">
      <Symbol name="info" className="mt-0.5 h-4 w-4 shrink-0 text-info-500" />
      <span className="min-w-0 wrap-anywhere">{skippedText(t, indexer)}</span>
      <Link to={INDEXERS_TAB_PATH} className="font-medium text-accent-400 hover:underline">
        {t('search.indexers.skippedLink')}
      </Link>
    </p>
  )
}

/** Je Indexer sein Zustand, seine Anfragen bisher und wie viele Releases jede gebracht hat. */
export function IndexerProgress({ indexers }: { indexers: readonly SearchIndexer[] }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language

  return (
    <ul aria-label={t('search.indexers.listLabel')} className="grid grid-cols-[minmax(0,1fr)] gap-3 lg:grid-cols-[repeat(2,minmax(0,1fr))]">
      {indexers.map((indexer) => (
        <li key={indexer.indexer_id} className="min-w-0">
          <Tile className="h-full">
            <TileHeader title={indexer.name} sub={releaseCountText(t, indexer.releases, language)}>
              <StateBadge state={indexer.state} />
            </TileHeader>
            {indexer.queries.length === 0 ? (
              <p className="text-sm text-mist-500">{t('search.indexers.noQueries')}</p>
            ) : (
              <ul className="flex flex-col gap-1">
                {indexer.queries.map((query, index) => (
                  <li key={`${query.kind}-${index}`} className="flex items-baseline justify-between gap-3 text-sm">
                    <span className="min-w-0 wrap-anywhere text-mist-200">{queryText(t, query)}</span>
                    <span className="shrink-0 text-mist-400 tabular-nums">{releaseCountText(t, query.releases, language)}</span>
                  </li>
                ))}
              </ul>
            )}
            {isSkipped(indexer) && <SkippedLine indexer={indexer} />}
            {isFailed(indexer) && (
              <p className="flex items-start gap-2 text-sm text-bad-500">
                <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
                <span className="min-w-0 wrap-anywhere">{failureText(t, indexer)}</span>
              </p>
            )}
          </Tile>
        </li>
      ))}
    </ul>
  )
}
