import { useTranslation } from 'react-i18next'

import type { SearchRelease, SearchVersion } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Badge } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { FitBadge } from '../checker/resultParts'
import { seriesFitState } from '../checker/seriesCheckerText'
import { Tile, TileHeader } from '../settings/parts'
import { SERIES_VERSIONS_TAB_PATH } from '../settings/tabs'
import type { VersionRow } from './searchOrder'
import { episodeCodesText, takeText } from './seriesSearchText'
import { TakesButton } from './TakesButton'
import { NoProfile, ReasonList } from './VersionOutcome'

/**
 * Das Ergebnis einer Suche nach einer Serie fuer eine Fassung (S3, Entscheidung 29): "würde nehmen" als
 * Zusammenstellung, eine Zeile je Release, dazu "nicht gefunden" fuer Folgen ohne Release und "kein passendes Release" fuer
 * Folgen, deren Releases nicht passen, mit deren Gruenden (Durchlauf ab null, 17.09.2026). Nimmt sie nichts,
 * heisst es "behält die vorhandenen Dateien" oder "nichts passt" mit den haeufigsten Gruenden. Fuellt ein Sonarr die
 * Fassung, sagt die Karte das; bewertet wird trotzdem. Ein ausgelassenes Staffelpaket bekommt einen Satz, warum.
 */
export function SeriesOutcome({ version, rows, releases }: { version: SearchVersion; rows: readonly VersionRow[]; releases: readonly SearchRelease[] }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  if (!version.has_profile) return <NoProfile label={version.label} path={SERIES_VERSIONS_TAB_PATH} />

  const takes = version.takes ?? []
  const notFound = version.not_found ?? []
  const noFit = version.no_fit ?? []
  const packsLeftOut = version.packs_left_out ?? []
  const inScope = rows.filter((row) => row.release.in_scope !== false)
  const badge =
    takes.length > 0 ? (
      <Badge tone="ok">
        <Symbol name="check" className="h-3.5 w-3.5" />
        {t('search.series.outcome.wouldTake')}
      </Badge>
    ) : version.keeps_current ? (
      <Badge tone="info">
        <Symbol name="shield" className="h-3.5 w-3.5" />
        {t('search.series.outcome.keepsCurrent')}
      </Badge>
    ) : notFound.length > 0 || noFit.length > 0 || version.nothing_fits.length > 0 || inScope.length > 0 ? (
      <Badge tone="bad">
        <Symbol name="alert" className="h-3.5 w-3.5" />
        {t('search.series.outcome.nothingFits')}
      </Badge>
    ) : (
      <Badge>{t('search.series.outcome.nothingToTake')}</Badge>
    )

  let sentence: string | null = null
  if (takes.length === 0) {
    if (version.keeps_current) sentence = t('search.series.outcome.keepsText')
    else if (rows.length === 0) sentence = t('search.series.outcome.noneFound')
    else if (notFound.length > 0 || noFit.length > 0 || version.nothing_fits.length > 0) sentence = t('search.series.outcome.noneFits')
    else sentence = t('search.series.outcome.nothingToTakeText')
  }

  return (
    <Tile>
      <TileHeader title={version.label}>{badge}</TileHeader>
      {version.fed_by && (
        <p className="flex items-start gap-2 text-sm text-mist-300">
          <Symbol name="info" className="mt-0.5 h-4 w-4 shrink-0 text-info-500" />
          <span className="min-w-0 wrap-anywhere">{t('search.series.outcome.fedBy', { name: version.fed_by })}</span>
        </p>
      )}
      {takes.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <h4 className="text-xs font-semibold text-mist-400">{t('search.series.outcome.takesTitle')}</h4>
          <ol aria-label={t('search.series.outcome.takesTitle')} className="flex flex-col gap-2">
            {takes.map((take) => {
              const release = releases.find((item) => item.release_key === take.release_key)
              const entry = release?.versions.find((item) => item.version_id === version.version_id)
              const result = entry?.series_result ?? null
              return (
                <li key={take.release_key} className="flex min-w-0 flex-col gap-1 rounded-lg border border-ink-700 bg-ink-900/60 px-3 py-2">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="min-w-0 text-sm text-mist-100">{takeText(t, take, release?.parsed_series, language)}</span>
                    {result && (
                      <span className="flex items-center gap-2">
                        <span className="text-xs text-mist-500 tabular-nums">{t('checker.score', { count: result.score, value: formatNumber(result.score, language) })}</span>
                        <FitBadge state={seriesFitState(result)} />
                      </span>
                    )}
                  </div>
                  {release && <p className="font-mono text-xs leading-5 wrap-anywhere text-mist-300">{release.title}</p>}
                  {take.covered_elsewhere.length > 0 && (
                    <p className="text-xs text-mist-500">{t('search.series.outcome.covered', { codes: episodeCodesText(t, take.covered_elsewhere, language) })}</p>
                  )}
                </li>
              )
            })}
          </ol>
          {packsLeftOut.map((item) => (
            <p key={item.release_key} className="text-xs text-mist-500">
              {t('search.series.outcome.packLeftOut', { brings: formatNumber(item.brings, language), episodes: formatNumber(item.episodes, language) })}
            </p>
          ))}
          <TakesButton version={version} releases={releases} />
        </div>
      )}
      {sentence && <p className="text-sm text-mist-200">{sentence}</p>}
      {notFound.length > 0 && (
        <p className="flex items-start gap-2 text-sm text-mist-200">
          <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0 text-bad-500" />
          <span className="min-w-0 wrap-anywhere">{t('search.series.outcome.notFound', { codes: episodeCodesText(t, notFound, language) })}</span>
        </p>
      )}
      {noFit.length > 0 && (
        <p className="flex items-start gap-2 text-sm text-mist-200">
          <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0 text-bad-500" />
          <span className="min-w-0 wrap-anywhere">{t('search.series.outcome.noFit', { codes: episodeCodesText(t, noFit, language) })}</span>
        </p>
      )}
      <ReasonList reasons={version.nothing_fits} />
    </Tile>
  )
}
