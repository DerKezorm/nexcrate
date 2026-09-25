import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { SeriesReleaseResult } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Badge, Button } from '../../components/ui'
import { formatAgeHours, formatNumber } from '../../lib/format'
import { sizeText } from '../../lib/size'
import { useMediaQuery } from '../../lib/useMediaQuery'
import { fitState, type FitState } from '../checker/checkerText'
import { FitBadge, ForNowHint, MatchedRules, RejectionList, UpgradeLine } from '../checker/resultParts'
import { EpisodeList } from '../checker/SeriesChecker'
import { matchNotes, schemeName, seriesFitState, seriesNoteText, seriesRejectionText } from '../checker/seriesCheckerText'
import { LoadButton } from './LoadButton'
import type { VersionRow } from './searchOrder'
import { peersText } from './searchText'
import { matchedEpisodesText } from './seriesSearchText'

/** So viele Releases stehen je Fassung zuerst da, der Rest hinter "alle zeigen". */
export const RELEASES_SHOWN = 10

/**
 * Die Grenzen, ab denen Spalten erscheinen: Klasse der Zelle, Klasse fuer den Ersatz unter dem Namen und dieselbe
 * Grenze als Medienabfrage. Es sind Tailwinds Vorgaben; `index.css` setzt kein `--breakpoint-*`, und Tailwind baut
 * daraus `min-width` in rem. Die Klassen stehen ausgeschrieben da, sonst findet Tailwind sie nicht.
 */
const BREAKPOINTS = {
  sm: { cell: 'hidden sm:table-cell', instead: 'sm:hidden', query: '(min-width: 40rem)' },
  lg: { cell: 'hidden lg:table-cell', instead: 'lg:hidden', query: '(min-width: 64rem)' },
  xl: { cell: 'hidden xl:table-cell', instead: 'xl:hidden', query: '(min-width: 80rem)' },
} as const

type Breakpoint = keyof typeof BREAKPOINTS

/**
 * Die Spalten neben Release, die immer steht, und ab welcher Grenze sie zu sehen sind. Kopf, Zeilen, der Ersatz
 * unter dem Namen und die Zeile mit den Gruenden lesen alle hier, damit sie nicht auseinanderlaufen.
 */
const COLUMNS = {
  rank: 'sm',
  // Seit S3, nur bei Serien.
  episodes: 'lg',
  indexer: 'xl',
  quality: 'lg',
  size: 'lg',
  age: 'xl',
  peers: 'lg',
  score: 'sm',
  result: 'sm',
} as const satisfies Record<string, Breakpoint>

type Column = keyof typeof COLUMNS

/**
 * Bei Serien stehen nur diese Spalten neben Release; alles andere steht unter dem Namen. Namen von Serien-Releases
 * sind lang, und in schmalen Spalten brach er auf vier Zeilen um (Test des Besitzers, 16.09.2026).
 */
const SERIES_COLUMNS: readonly Column[] = ['rank', 'score', 'result']

function shownFor(column: Column, series: boolean): boolean {
  return series ? SERIES_COLUMNS.includes(column) : column !== 'episodes'
}

/** Klasse einer Zelle, die erst ab der Grenze ihrer Spalte zu sehen ist. */
function cell(column: Column): string {
  return BREAKPOINTS[COLUMNS[column]].cell
}

/** Klasse fuer das, was unter dem Namen steht, solange die Spalte fehlt. Bei Serien immer, wenn die Spalte fehlt. */
function instead(column: Column, series = false): string {
  return series && !SERIES_COLUMNS.includes(column) ? '' : BREAKPOINTS[COLUMNS[column]].instead
}

/**
 * Wie viele Spalten gerade zu sehen sind. Die Zeile mit den Gruenden spannt genau ueber sie: Ein groesseres
 * `colSpan` legt in der `table-fixed`-Tabelle unsichtbare Spalten an, die Breite nehmen, und Release schrumpft
 * auf wenige Pixel. Gemessen unter 1280 px; am Telefon traf es auch den Satz zu "Vorerst".
 */
function useVisibleColumns(series: boolean): number {
  const shown: Record<Breakpoint, boolean> = {
    sm: useMediaQuery(BREAKPOINTS.sm.query),
    lg: useMediaQuery(BREAKPOINTS.lg.query),
    xl: useMediaQuery(BREAKPOINTS.xl.query),
  }
  // Eins fuer Release.
  return 1 + (Object.entries(COLUMNS) as [Column, Breakpoint][]).filter(([column, from]) => shownFor(column, series) && shown[from]).length
}

/**
 * Die Releases einer Fassung in ihrer Reihenfolge. Am Telefon steht nur die Spalte Release da, mit
 * einer Zeile darunter fuer das, was sonst in eigenen Spalten steht. Gruende und Regeln auf Klick.
 * ⚠️ Kein Download- und kein Info-Link: Die Antwort hat keine. Seit Schritt 3 laedt "Laden" in den Gruenden das Release,
 * nexcrate holt die Datei dabei selbst.
 *
 * Seit S3 auch fuer Serien (`series`): eine Spalte Folgen, das Ergebnis des Serien-Pruefers und statt "Laden" ein
 * ausgegrauter Knopf, bis S4 das Laden bringt.
 */
export function VersionReleases({
  label,
  rows,
  onlyFitting,
  series = false,
  takenKey = null,
}: {
  label: string
  rows: readonly VersionRow[]
  onlyFitting: boolean
  series?: boolean
  /** Das Release, das die Karte "Würde nehmen" schon anbietet: in seiner Zeile steht kein zweiter Knopf. */
  takenKey?: string | null
}) {
  const { t, i18n } = useTranslation()
  const [all, setAll] = useState(false)
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set())
  const columns = useVisibleColumns(series)
  const heading = t('search.releases.title', { label })
  const visible = all ? rows : rows.slice(0, RELEASES_SHOWN)

  function toggle(key: string) {
    setOpen((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  return (
    <div className="flex flex-col gap-2">
      <h4 className="text-sm font-semibold text-mist-300">{heading}</h4>
      {rows.length === 0 ? (
        <p className="text-sm text-mist-500">{onlyFitting ? t('search.releases.noneFitting') : series ? t('search.series.releases.none') : t('search.releases.none')}</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-ink-700">
          <table className="w-full table-fixed text-sm" aria-label={heading}>
            <thead className="bg-ink-900 text-left text-xs text-mist-500">
              <tr>
                <th scope="col" className={`${cell('rank')} w-14 px-3 py-2 font-medium`}>
                  {t('search.releases.columnRank')}
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  {t('search.releases.columnTitle')}
                </th>
                {!series && (
                  <>
                    <th scope="col" className={`${cell('indexer')} w-32 px-3 py-2 font-medium`}>
                      {t('search.releases.columnIndexer')}
                    </th>
                    <th scope="col" className={`${cell('quality')} w-32 px-3 py-2 font-medium`}>
                      {t('search.releases.columnQuality')}
                    </th>
                    <th scope="col" className={`${cell('size')} w-24 px-3 py-2 text-right font-medium`}>
                      {t('search.releases.columnSize')}
                    </th>
                    <th scope="col" className={`${cell('age')} w-32 px-3 py-2 font-medium`}>
                      {t('search.releases.columnAge')}
                    </th>
                    <th scope="col" className={`${cell('peers')} w-28 px-3 py-2 text-right font-medium`}>
                      {t('search.releases.columnPeers')}
                    </th>
                  </>
                )}
                <th scope="col" className={`${cell('score')} w-24 px-3 py-2 text-right font-medium`}>
                  {t('search.releases.columnScore')}
                </th>
                <th scope="col" className={`${cell('result')} w-36 px-3 py-2 font-medium`}>
                  {t('search.releases.columnResult')}
                </th>
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => (
                <ReleaseRow
                  key={row.release.release_key}
                  row={row}
                  series={series}
                  columns={columns}
                  canLoad={row.release.release_key !== takenKey}
                  open={open.has(row.release.release_key)}
                  onToggle={() => toggle(row.release.release_key)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {rows.length > RELEASES_SHOWN && (
        <div>
          <Button variant="ghost" size="sm" onClick={() => setAll((current) => !current)} aria-expanded={all}>
            {all ? t('search.releases.showFewer') : t('search.releases.showAll', { value: formatNumber(rows.length, i18n.language) })}
          </Button>
        </div>
      )}
    </div>
  )
}

function ReleaseRow({
  row,
  series,
  columns,
  canLoad,
  open,
  onToggle,
}: {
  row: VersionRow
  series: boolean
  columns: number
  canLoad: boolean
  open: boolean
  onToggle: () => void
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const detailsId = useId()
  const hintId = useId()
  const { release, entry } = row
  const result = series ? null : entry.result
  const seriesResult = series ? (entry.series_result ?? null) : null
  const scored = result ?? seriesResult
  const state = result ? fitState(result) : seriesResult ? seriesFitState(seriesResult) : null
  const quality = (series ? release.parsed_series?.quality : release.parsed?.quality) ?? null
  const episodes = series ? matchedEpisodesText(t, release.parsed_series, release.match?.episodes ?? [], language) : null
  // Eine andere Lesart nach TMDB aendert nichts mehr (die erste Nummerierung entscheidet); sie steht nur unter den Gruenden.
  const notes = series && release.match ? matchNotes(t, { ...release.match, ambiguous: false }, language) : []
  const size = release.size_bytes !== null ? sizeText(t, release.size_bytes, language) : null
  const age = release.age_hours !== null ? formatAgeHours(release.age_hours, language) : null
  const peers = peersText(t, release, language)
  const score = scored ? formatNumber(scored.score, language) : null
  const rank = entry.rank !== null ? formatNumber(entry.rank, language) : null
  // Die Marke steht zweimal im Baum, unter dem Namen fuers Telefon und in der Spalte. Bei "Vorerst"
  // zeigen beide auf denselben verborgenen Satz; offen steht er unter "Gruende und Regeln".
  const fit = state === null ? null : <FitBadge state={state} describedBy={hintId} />

  return (
    <>
      <tr className="border-t border-ink-700/60 align-top">
        <td className={`${cell('rank')} px-3 py-2 text-mist-400 tabular-nums`}>{rank}</td>
        <td className="px-3 py-2">
          <p className="font-mono text-xs leading-5 wrap-anywhere text-mist-200">{release.title}</p>
          <p className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-mist-500">
            {rank !== null && <span className={instead('rank', series)}>{t('search.releases.rank', { rank })}</span>}
            {episodes && <span className={`${instead('episodes', series)} font-mono text-mist-300`}>{episodes}</span>}
            <span className={`${instead('indexer', series)} wrap-anywhere`}>{release.indexer}</span>
            {quality && <span className={instead('quality', series)}>{quality}</span>}
            {size && <span className={instead('size', series)}>{size}</span>}
            {age && <span className={instead('age', series)}>{age}</span>}
            {peers && <span className={instead('peers', series)}>{peers}</span>}
            {scored && score !== null && <span className={instead('score', series)}>{t('checker.score', { count: scored.score, value: score })}</span>}
          </p>
          {notes.map((note) => (
            <p key={note} className="mt-0.5 flex items-start gap-1.5 text-xs text-mist-400">
              <Symbol name="info" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span className="min-w-0">{note}</span>
            </p>
          ))}
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            {fit && <span className={instead('result')}>{fit}</span>}
            {series && release.in_scope === false && <Badge>{t('search.series.releases.otherEpisodes')}</Badge>}
            {release.blocklisted === true && (
              <Badge>
                <Symbol name="close" className="h-3.5 w-3.5" />
                {t('search.load.blocklisted')}
              </Badge>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={onToggle}
              aria-expanded={open}
              aria-controls={open ? detailsId : undefined}
              aria-label={t('search.releases.detailsLabel', { title: release.title })}
            >
              <Symbol name={open ? 'chevronDown' : 'chevron'} className="h-3.5 w-3.5" />
              {t('search.releases.details')}
            </Button>
            {/* Laden steht in der Zeile selbst, nicht erst hinter den Gruenden: Wer ein anderes Release nehmen will,
                soll es sehen (Rueckmeldung 20.09.2026). Ein Release, das nicht passt oder gesperrt ist, fragt nach.
                Das Release der Karte "Würde nehmen" hat dort schon seinen Knopf. */}
            {canLoad && <LoadButton release={release} entry={entry} />}
          </div>
        </td>
        {!series && (
          <>
            <td className={`${cell('indexer')} px-3 py-2 wrap-anywhere text-mist-300`}>{release.indexer}</td>
            <td className={`${cell('quality')} px-3 py-2 font-mono text-xs leading-5 wrap-anywhere text-mist-300`}>{quality}</td>
            <td className={`${cell('size')} px-3 py-2 text-right text-mist-300 tabular-nums`}>{size}</td>
            <td className={`${cell('age')} px-3 py-2 text-mist-400`}>{age}</td>
            <td className={`${cell('peers')} px-3 py-2 text-right text-mist-300 tabular-nums`}>{peers}</td>
          </>
        )}
        <td className={`${cell('score')} px-3 py-2 text-right text-mist-200 tabular-nums`}>{score}</td>
        <td className={`${cell('result')} px-3 py-2`}>
          {fit}
          {state === 'forNow' && (
            <span id={hintId} hidden>
              {t('checker.forNowHint')}
            </span>
          )}
        </td>
      </tr>
      {open && (
        <tr id={detailsId} className="align-top">
          <td colSpan={columns} className="px-3 pb-3">
            {result && (
              <div className="flex flex-col gap-3 rounded-xl border border-ink-700 bg-ink-900/60 p-3">
                <RejectionList rejections={result.rejections} />
                {state === 'forNow' && <ForNowHint />}
                {/* Wie im Pruefer: Nur ein Release, das passt, kann eine Datei ersetzen. */}
                {result.accepted && result.upgrade && <UpgradeLine upgrade={result.upgrade} />}
                <MatchedRules matched={result.matched} />
              </div>
            )}
            {seriesResult && (
              <SeriesDetails result={seriesResult} state={state} otherReading={release.match?.ambiguous === true && !release.match.notes.includes('group_counting') ? (release.match.via ?? null) : null} />
            )}
          </td>
        </tr>
      )}
    </>
  )
}

/** Gruende und Regeln eines Releases einer Serie, wie im Serien-Pruefer. "Laden" steht in der Zeile. */
function SeriesDetails({
  result,
  state,
  otherReading,
}: {
  result: SeriesReleaseResult
  state: FitState | null
  otherReading: string | null
}) {
  const { t, i18n } = useTranslation()
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-ink-700 bg-ink-900/60 p-3">
      <RejectionList rejections={result.rejections} toText={(rejection, language) => seriesRejectionText(t, rejection, language)} />
      {otherReading !== null && (
        <p className="flex items-start gap-2 text-xs text-mist-400">
          <Symbol name="info" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span className="min-w-0">{t('search.series.releases.otherReading', { via: schemeName(t, otherReading) })}</span>
        </p>
      )}
      {state === 'forNow' && <ForNowHint text={result.below_target_in_group ? t('checker.series.forNowHint') : undefined} />}
      {result.notes.map((note, index) => (
        <p key={`${note.code}-${index}`} className="flex items-start gap-2 text-xs text-mist-400">
          <Symbol name="info" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span className="min-w-0">{seriesNoteText(t, note)}</span>
        </p>
      ))}
      {result.episodes.length > 0 && <EpisodeList result={result} language={i18n.language} />}
      <MatchedRules matched={result.matched} />
    </div>
  )
}
