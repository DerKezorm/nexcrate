import { type ReactNode, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { AlbumSearch, AlbumSearchDecision, AlbumSearchRelease } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Section, Spinner } from '../../components/ui'
import { formatAgeHours, formatNumber, formatTime } from '../../lib/format'
import { musicStepText } from '../../lib/musicSteps'
import { sizeText } from '../../lib/size'
import { albumNoteText, albumRejectionText } from '../checker/musicCheckerText'
import { Detail, Tile, TileHeader } from '../settings/parts'
import { type AlbumLoaded, AlbumLoadButton } from './AlbumLoadButton'
import { NoProfile } from './VersionOutcome'
import { albumReasonCountText, kindText, matchText, peersOf } from './albumSearchText'
import { IndexerProgress, SkippedLine } from './IndexerProgress'
import { failureText, isFailed, isSkipped, pollProblemText, startProblemText } from './searchText'
import type { SearchProblem } from './useTitleSearch'

const MUSIC_VERSIONS_PATH = '/einstellungen?reiter=fassungen&unter=musik'

/**
 * Die Suche auf der Albumseite (M3). Waehrend sie laeuft: je Indexer Zustand und Anfragen. Danach die Karte der
 * Musik-Fassung mit dem, was nexcrate nehmen wuerde, darunter die Releases dieses Albums mit Urteil, die Releases
 * anderer Alben des Kuenstlers mit Verweis und zugeklappt alles, was nicht passt. Seit M4 laedt "Laden" ein Release dieses
 * Albums in die Musik-Fassung.
 */
export function AlbumSearchSection({
  search,
  starting,
  problem,
  onAliases,
  busy,
}: {
  search: AlbumSearch | null
  starting: boolean
  problem: SearchProblem | null
  /** Noch einmal suchen, mit weiteren Namen des Kuenstlers. */
  onAliases: () => void
  busy: boolean
}) {
  const { t } = useTranslation()
  return (
    <Section title={t('search.title')} intro={t('search.album.intro')}>
      {problem && <FormMessage>{problem.stage === 'start' ? startProblemText(t, problem.error, false) : pollProblemText(t, problem.error)}</FormMessage>}
      {search === null ? (
        starting &&
        problem === null && (
          <p role="status" className="flex items-center gap-2 text-sm text-mist-400">
            <Spinner />
            {t('search.starting')}
          </p>
        )
      ) : search.state === 'done' ? (
        <AlbumResults key={search.search_id} search={search} onAliases={onAliases} busy={busy} />
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

function AlbumResults({ search, onAliases, busy }: { search: AlbumSearch; onAliases: () => void; busy: boolean }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const [queries, setQueries] = useState(false)
  const [loaded, setLoaded] = useState<AlbumLoaded>(null)
  const ours = search.releases.filter((release) => release.match.kind === 'this')
  const others = search.releases.filter((release) => release.match.kind === 'other_album')
  const rest = search.releases.filter((release) => release.match.kind !== 'this' && release.match.kind !== 'other_album')
  const failed = search.indexers.filter(isFailed)
  const skipped = search.indexers.filter(isSkipped)
  const decision = search.versions[0] ?? null
  const summary = t('search.summary', {
    indexers: t('search.summaryIndexers', { count: search.indexers.length, value: formatNumber(search.indexers.length, language) }),
    releases: t('search.album.summaryReleases', { count: ours.length, value: formatNumber(ours.length, language) }),
  })
  const accepted = ours.filter((release) => release.verdict?.accepted)
  const askAliases = accepted.length === 0 && !search.scope.aliases

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-0.5">
        <p className="text-sm text-mist-200">{summary}</p>
        {search.finished_at && <p className="text-xs text-mist-500">{t('search.doneAt', { time: formatTime(search.finished_at, language) })}</p>}
        {search.scope.aliases && <p className="text-xs text-mist-500">{t('search.album.withAliases')}</p>}
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

      {decision !== null && <AlbumOutcome decision={decision} releases={ours} searchId={search.search_id} loaded={loaded} onLoaded={setLoaded} />}

      {askAliases && (
        <div className="flex flex-col gap-2">
          <div>
            <Button variant="ghost" size="sm" onClick={onAliases} loading={busy}>
              {!busy && <Symbol name="search" />}
              {t('search.album.aliases')}
            </Button>
          </div>
          <p className="text-xs text-mist-500">{t('search.album.aliasesHint')}</p>
        </div>
      )}

      <section aria-label={t('search.album.oursTitle')} className="flex flex-col gap-3 border-t border-ink-700 pt-5">
        <h3 className="text-sm font-semibold text-mist-200">{t('search.album.oursTitle')}</h3>
        {ours.length === 0 ? (
          <p className="text-sm text-mist-400">{t('search.album.oursNone')}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {ours.map((release) => (
              <li key={release.release_key}>
                <ReleaseRow release={release} taken={decision?.would_take === release.release_key}>
                  {decision !== null && decision.has_profile && (
                    <AlbumLoadButton searchId={search.search_id} release={release} decision={decision} loaded={loaded} onLoaded={setLoaded} />
                  )}
                </ReleaseRow>
              </li>
            ))}
          </ul>
        )}
      </section>

      {others.length > 0 && (
        <section aria-label={t('search.album.othersTitle')} className="flex flex-col gap-3 border-t border-ink-700 pt-5">
          <h3 className="text-sm font-semibold text-mist-200">{t('search.album.othersTitle')}</h3>
          <ul className="flex flex-col gap-2">
            {others.map((release) => (
              <li key={release.release_key} className="flex min-w-0 flex-col gap-0.5 text-sm">
                <span className="font-mono wrap-anywhere text-mist-100">{release.title}</span>
                <span className="text-mist-400">
                  {t('search.album.belongsTo')}{' '}
                  {release.match.title_id !== null ? (
                    <Link to={`/titel/${release.match.title_id}`} className="font-medium text-accent-400 hover:underline">
                      {albumName(t, release)}
                    </Link>
                  ) : (
                    albumName(t, release)
                  )}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {rest.length > 0 && (
        <details className="border-t border-ink-700 pt-5">
          <summary className="cursor-pointer text-sm font-semibold text-mist-200">
            {t('search.album.restTitle', { count: rest.length, value: formatNumber(rest.length, language) })}
          </summary>
          <ul className="mt-3 flex flex-col gap-2">
            {rest.map((release) => (
              <li key={release.release_key} className="flex min-w-0 flex-col gap-0.5 text-sm">
                <span className="font-mono wrap-anywhere text-mist-100">{release.title}</span>
                <span className="text-mist-400">{matchText(t, release.match)}</span>
              </li>
            ))}
          </ul>
        </details>
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
  )
}

function albumName(t: ReturnType<typeof useTranslation>['t'], release: AlbumSearchRelease): string {
  const name = release.match.album ?? release.match.read_album
  return release.match.year !== null ? t('search.album.nameYear', { name, year: release.match.year }) : name
}

/** Die Karte der Musik-Fassung: wuerde nehmen, behaelt, nichts passt, oder ohne Profil der Weg dorthin. */
function AlbumOutcome({
  decision,
  releases,
  searchId,
  loaded,
  onLoaded,
}: {
  decision: AlbumSearchDecision
  releases: AlbumSearchRelease[]
  searchId: string
  loaded: AlbumLoaded
  onLoaded: (loaded: NonNullable<AlbumLoaded>) => void
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  if (!decision.has_profile) return <NoProfile label={decision.label} path={MUSIC_VERSIONS_PATH} />
  const taken = decision.would_take !== null ? releases.find((release) => release.release_key === decision.would_take) : undefined
  if (taken !== undefined) {
    const forNow = taken.verdict?.for_now === true
    const facts = [
      { label: t('search.outcome.indexer'), value: taken.indexer_name },
      { label: t('search.album.step'), value: taken.step !== null ? musicStepText(t, taken.step) : null },
      { label: t('search.outcome.size'), value: taken.size_bytes !== null ? sizeText(t, taken.size_bytes, language) : null },
      { label: t('search.outcome.age'), value: taken.age_hours !== null ? formatAgeHours(taken.age_hours, language) : null },
      { label: t('search.outcome.peers'), value: peersOf(t, taken, language) },
    ].filter((fact) => fact.value !== null && fact.value !== '')
    return (
      <Tile>
        <TileHeader title={decision.label}>
          <Badge tone={forNow ? 'accent' : 'ok'}>
            <Symbol name={forNow ? 'clock' : 'check'} className="h-3.5 w-3.5" />
            {forNow ? t('search.outcome.wouldTakeForNow') : t('search.outcome.wouldTake')}
          </Badge>
        </TileHeader>
        <p className="font-mono text-sm leading-5 wrap-anywhere text-mist-100">{taken.title}</p>
        {facts.length > 0 && (
          <dl className="grid grid-cols-[repeat(2,minmax(0,1fr))] gap-3 sm:grid-cols-[repeat(3,minmax(0,1fr))] lg:grid-cols-[repeat(5,minmax(0,1fr))]">
            {facts.map((fact) => (
              <Detail key={fact.label} label={fact.label}>
                {fact.value}
              </Detail>
            ))}
          </dl>
        )}
        <p className="text-sm text-mist-200">
          {taken.place === null || taken.place <= 1 ? t('search.outcome.firstPlace') : t('search.outcome.place', { rank: formatNumber(taken.place, language) })}
        </p>
        <AlbumLoadButton searchId={searchId} release={taken} decision={decision} loaded={loaded} onLoaded={onLoaded} prominent />
      </Tile>
    )
  }
  if (decision.keeps_current) {
    return (
      <Tile>
        <TileHeader title={decision.label}>
          <Badge tone="info">
            <Symbol name="shield" className="h-3.5 w-3.5" />
            {t('search.outcome.keepsCurrent')}
          </Badge>
        </TileHeader>
        <p className="text-sm text-mist-200">{t('search.album.keeps')}</p>
      </Tile>
    )
  }
  return (
    <Tile>
      <TileHeader title={decision.label}>
        <Badge tone="bad">
          <Symbol name="alert" className="h-3.5 w-3.5" />
          {t('search.outcome.nothingFits')}
        </Badge>
      </TileHeader>
      <p className="text-sm text-mist-200">{releases.length > 0 ? t('search.album.noneFits') : t('search.album.noneFound')}</p>
      {decision.nothing_fits.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <h4 className="text-xs font-semibold text-mist-400">{t('search.outcome.reasonsTitle')}</h4>
          <ul className="flex flex-col gap-1">
            {decision.nothing_fits.map((reason) => (
              <li key={reason.code} className="flex items-start gap-2 text-sm text-mist-200">
                <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0 text-bad-500" />
                <span className="min-w-0 wrap-anywhere">{albumReasonCountText(t, reason, language)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Tile>
  )
}

/** Ein Release dieses Albums: Name, Stufe, Groesse, Indexer, Urteil mit Gruenden und Hinweisen. */
function ReleaseRow({ release, taken, children }: { release: AlbumSearchRelease; taken: boolean; children?: ReactNode }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const verdict = release.verdict
  const facts = [
    release.step !== null ? musicStepText(t, release.step) : musicStepText(t, 'unknown'),
    release.size_bytes !== null ? sizeText(t, release.size_bytes, language) : null,
    release.indexer_name,
    release.age_hours !== null ? formatAgeHours(release.age_hours, language) : null,
    peersOf(t, release, language),
  ].filter((fact): fact is string => fact !== null && fact !== '')
  const badge =
    verdict === null ? null : !verdict.judged ? (
      verdict.accepted ? null : (
        <Badge tone="bad">
          <Symbol name="alert" className="h-3.5 w-3.5" />
          {t('search.album.refused')}
        </Badge>
      )
    ) : verdict.accepted ? (
      <Badge tone={verdict.for_now ? 'accent' : 'ok'}>
        <Symbol name={verdict.for_now ? 'clock' : 'check'} className="h-3.5 w-3.5" />
        {verdict.for_now ? t('search.album.forNow') : t('search.album.fits')}
      </Badge>
    ) : (
      <Badge tone="bad">
        <Symbol name="alert" className="h-3.5 w-3.5" />
        {t('search.album.refused')}
      </Badge>
    )
  return (
    <div className={`flex min-w-0 flex-col gap-1 rounded-xl border p-3 ${taken ? 'border-ok-500/40 bg-ok-500/5' : 'border-ink-700 bg-ink-900'}`}>
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
        <span className="min-w-0 font-mono text-sm wrap-anywhere text-mist-100">{release.title}</span>
        <span className="flex shrink-0 items-center gap-2">
          {release.place !== null && verdict?.judged && <span className="text-xs text-mist-400">{t('search.album.place', { place: formatNumber(release.place, language) })}</span>}
          {badge}
        </span>
      </div>
      <p className="text-xs text-mist-400">{facts.join(' · ')}</p>
      {release.match.kind_differs !== null && <p className="text-xs text-mist-400">{t('search.album.kindDiffers', { kind: kindText(t, release.match.kind_differs) })}</p>}
      {verdict?.rejections.map((rejection) => (
        <p key={`r-${rejection.code}`} className="flex items-start gap-1.5 text-xs text-bad-500">
          <Symbol name="alert" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span className="min-w-0 wrap-anywhere">{albumRejectionText(t, rejection, language)}</span>
        </p>
      ))}
      {verdict?.notes.map((note) => (
        <p key={`n-${note.code}`} className="flex items-start gap-1.5 text-xs text-mist-300">
          <Symbol name="info" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-info-500" />
          <span className="min-w-0 wrap-anywhere">{albumNoteText(t, note, language)}</span>
        </p>
      ))}
      {children}
    </div>
  )
}
