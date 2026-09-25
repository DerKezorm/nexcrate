import { Fragment } from 'react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { Episode, EpisodeInVersion, EpisodeNumber, TitleVersion } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Button } from '../../components/ui'
import { formatCalendarDate, formatNumber } from '../../lib/format'
import { sizeText } from '../../lib/size'
import { DOWNLOADS_PROBLEMS_PATH } from '../downloads/address'
import { EpisodeCorrection } from './EpisodeCorrection'
import { InlineSwitch } from './InlineSwitch'
import { definitionIdOf, episodeCode, isFed } from './seriesText'

/** Der Name einer Nummerierung. Eine unbekannte steht da, wie sie kommt. */
function schemeText(t: TFunction, scheme: string): string {
  switch (scheme) {
    case 'tvdb':
      return t('series.episode.tvdb')
    case 'scene':
      return t('series.episode.scene')
    // Seit S3: die lokale Korrektur und die gewaehlte Episodengruppe.
    case 'owner':
      return t('series.episode.owner')
    case 'group':
      return t('series.episode.groupScheme')
    // Seit Anime A1: die Durchzaehlung einer Anime-Serie.
    case 'absolute':
      return t('series.episode.absolute')
    default:
      return scheme
  }
}

function numberText(number: EpisodeNumber): string | null {
  if (typeof number.season === 'number' && typeof number.episode === 'number') return episodeCode(number.season, number.episode, number.episode_end)
  return typeof number.absolute === 'number' ? String(number.absolute) : null
}

/**
 * Die Saetze zum Zustand einer Folge in einer Fassung: laedt, haengt, kommt noch, fehlt in Sonarr, nachtraeglich. Eine
 * eigene Fassung laedt mit nexcrate: dann ohne Namen einer Verbindung, mit Fortschritt (seit der Durchsicht von S4).
 */
function stateSentences(t: TFunction, episode: Episode, entry: EpisodeInVersion, name: string, language: string, fed: boolean): string[] {
  const sentences: string[] = []
  if (entry.state === 'downloading') {
    if (fed) sentences.push(t('series.episode.downloading', { name }))
    else if (typeof entry.progress === 'number') sentences.push(t('series.episode.downloadingOwnPercent', { percent: formatNumber(Math.round(entry.progress), language) }))
    else sentences.push(t('series.episode.downloadingOwn'))
  }
  if (entry.state === 'problem') sentences.push(fed ? t('series.episode.problem', { name }) : t('series.episode.problemOwn'))
  if (!episode.aired && episode.air_date && entry.file === null) sentences.push(t('series.episode.future', { date: formatCalendarDate(episode.air_date, language) }))
  if (entry.in_source === false) sentences.push(t('series.episode.notInSource', { name }))
  if (entry.late) sentences.push(t('series.episode.late'))
  return sentences
}

/**
 * Eine aufgeklappte Folge: worum es geht, ihre Nummern je Zaehlweise und je Fassung Datei, Schalter und Zustand. Eine
 * Fassung aus Sonarr zeigt statt des Schalters, wie Sonarr ihn gesetzt hat.
 *
 * Seit S3 "Diese Folge suchen" (`onSearch`) und die lokale Korrektur der Nummer, unter der Releases die Folge fuehren.
 * Beides nur, wenn die Seite es anbietet (`correction`).
 */
export function EpisodePanel({
  episode,
  versions,
  busy,
  onSwitch,
  onSearch,
  searchBlocked = false,
  correction,
  onRelease,
  onDeleteFile,
}: {
  episode: Episode
  versions: readonly TitleVersion[]
  busy: boolean
  onSwitch: (versionId: number, watched: boolean) => void
  onSearch?: () => void
  searchBlocked?: boolean
  correction?: { titleId: number; seasonEpisodes: readonly Episode[]; anime?: boolean; onSaved: () => void }
  /** Seit 18.09.2026: eine verknuepfte Datei einer eigenen Fassung loesen; sie steht danach unter "Nicht zugeordnet". */
  onRelease?: (versionId: number, fileId: number) => void
  /** /api/v1 V2: eine Folgendatei einer eigenen Fassung in den Papierkorb. */
  onDeleteFile?: (version: TitleVersion, fileId: number, fileName: string) => void
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const code = episodeCode(episode.season_number, episode.number)
  const numbers = [
    { label: t('series.episode.tmdb'), value: code, verified: true },
    { label: t('series.episode.date'), value: episode.air_date ? formatCalendarDate(episode.air_date, language) : t('series.seasons.noDate'), verified: true },
    // Seit 18.09.2026: der englische Titel, wie ihn Sonarr und die Dateien meist tragen.
    ...(episode.name_en ? [{ label: t('series.episode.english'), value: episode.name_en, verified: true }] : []),
    ...episode.numbers
      .map((number) => ({ label: schemeText(t, number.scheme), value: numberText(number), verified: number.verified }))
      .filter((entry): entry is { label: string; value: string; verified: boolean } => entry.value !== null),
  ]

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4 md:grid-cols-[repeat(2,minmax(0,1fr))]">
      <div className="flex min-w-0 flex-col gap-3">
        {onSearch && (
          <div>
            <Button size="sm" variant="ghost" onClick={onSearch} disabled={searchBlocked}>
              <Symbol name="search" />
              {t('search.series.episodeAction')}
            </Button>
          </div>
        )}
        {episode.tmdb_gone && (
          <p className="flex items-start gap-2 text-sm text-mist-400">
            <Symbol name="info" className="mt-0.5 h-4 w-4 shrink-0 text-info-500" />
            <span className="min-w-0">{t('series.episode.gone')}</span>
          </p>
        )}
        <div className="flex flex-col gap-1">
          <h4 className="text-sm font-semibold text-mist-200">{t('series.episode.overview')}</h4>
          <p className="text-sm leading-relaxed text-mist-400">{episode.overview || t('series.episode.noOverview')}</p>
        </div>
        <div className="flex flex-col gap-1">
          <h4 className="text-sm font-semibold text-mist-200">{t('series.episode.numbers')}</h4>
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
            {numbers.map((entry, index) => (
              <Fragment key={`${entry.label}-${index}`}>
                <dt className="text-mist-500">{entry.label}</dt>
                <dd className="text-mist-200 tabular-nums wrap-anywhere">
                  {entry.value}
                  {!entry.verified && <span className="ml-2 text-xs text-mist-500">{t('series.episode.unverified')}</span>}
                </dd>
              </Fragment>
            ))}
          </dl>
        </div>
        {correction && <EpisodeCorrection key={episode.id} titleId={correction.titleId} episode={episode} seasonEpisodes={correction.seasonEpisodes} anime={correction.anime} onSaved={correction.onSaved} />}
      </div>

      <div className="flex min-w-0 flex-col gap-2">
        {versions.map((version) => {
          const versionId = definitionIdOf(version)
          const entry = episode.versions.find((item) => item.version_id === versionId) ?? null
          if (versionId === null || entry === null) return null
          const fed = isFed(version)
          const name = version.source_name ?? ''
          const file = entry.file
          const sentences = stateSentences(t, episode, entry, name, language, fed)
          return (
            <div key={version.id} className="flex min-w-0 flex-col gap-2 rounded-xl border border-ink-700 bg-ink-900/60 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="flex min-w-0 items-center gap-2 font-semibold text-mist-100 wrap-anywhere">
                  <Symbol name="layers" className="h-4 w-4 shrink-0 text-accent-400" />
                  {version.label}
                </span>
                {!fed && (
                  <InlineSwitch
                    text={t('series.episode.watched')}
                    label={t('series.episode.watchedLabel', { label: version.label, number: code })}
                    checked={entry.watched}
                    disabled={busy}
                    onChange={(watched) => onSwitch(versionId, watched)}
                  />
                )}
              </div>
              {fed && <p className="text-xs text-mist-500">{entry.watched ? t('series.episode.fed', { name }) : t('series.episode.fedOff', { name })}</p>}
              {sentences.map((sentence) => (
                <p key={sentence} className={'text-sm ' + (entry.state === 'problem' ? 'text-bad-500' : 'text-mist-400')}>
                  {sentence}
                </p>
              ))}
              {!fed && entry.state === 'problem' && (
                <Link to={DOWNLOADS_PROBLEMS_PATH} className="text-sm font-medium text-accent-400 hover:underline">
                  {t('series.episode.toProblems')}
                </Link>
              )}
              {file === null ? (
                <p className="text-sm text-mist-500">{t('series.episode.noFile')}</p>
              ) : (
                <>
                  <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
                    <dt className="text-mist-500">{file.second_part ? t('series.episode.part1') : t('series.episode.file')}</dt>
                    <dd className="font-mono text-xs leading-5 wrap-anywhere text-mist-300">{file.relative_path}</dd>
                    {/* Doppelfolge in zwei Dateien: Teil 2 steht direkt darunter. */}
                    {file.second_part && (
                      <>
                        <dt className="text-mist-500">{t('series.episode.part2')}</dt>
                        <dd className="font-mono text-xs leading-5 wrap-anywhere text-mist-300">{file.second_part.relative_path}</dd>
                      </>
                    )}
                    {/* Der Name vor dem Umbenennen: daran sieht man, welche Folge das Release selbst zu sein behauptete. */}
                    {file.release_title && (
                      <>
                        <dt className="text-mist-500">{t('series.episode.releaseName')}</dt>
                        <dd className="font-mono text-xs leading-5 wrap-anywhere text-mist-300">{file.release_title}</dd>
                      </>
                    )}
                    {file.quality && (
                      <>
                        <dt className="text-mist-500">{t('series.episode.quality')}</dt>
                        <dd className="text-mist-200 wrap-anywhere">{file.quality}</dd>
                      </>
                    )}
                    {file.size_bytes > 0 && (
                      <>
                        <dt className="text-mist-500">{t('series.episode.size')}</dt>
                        <dd className="text-mist-200 tabular-nums">{sizeText(t, file.size_bytes + (file.second_part?.size_bytes ?? 0), language)}</dd>
                      </>
                    )}
                    {file.release_group && (
                      <>
                        <dt className="text-mist-500">{t('series.episode.group')}</dt>
                        <dd className="text-mist-200 wrap-anywhere">{file.release_group}</dd>
                      </>
                    )}
                  </dl>
                  {file.episodes > 1 && <p className="text-xs text-mist-500">{t('series.episode.covers', { count: file.episodes })}</p>}
                  {!fed && (onRelease || onDeleteFile) && (
                    <div className="flex flex-wrap gap-2">
                      {onRelease && (
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={busy}
                          aria-label={t('series.episode.releaseLabel', { label: version.label, number: code })}
                          onClick={() => onRelease(versionId, file.id)}
                        >
                          <Symbol name="swap" />
                          {t('series.episode.release')}
                        </Button>
                      )}
                      {onDeleteFile && (
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={busy}
                          aria-label={t('series.episode.deleteFileLabel', { label: version.label, number: code })}
                          onClick={() => onDeleteFile(version, file.id, file.relative_path.split('/').pop() ?? file.relative_path)}
                        >
                          <Symbol name="trash" />
                          {t('series.episode.deleteFile')}
                        </Button>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
