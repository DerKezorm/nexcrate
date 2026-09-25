import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { AUTOMATIC_POLL_MAX, AUTOMATIC_POLL_MS, automaticApi } from '../../api/automatic'
import { ApiError, errorText } from '../../api/client'
import type { FolderReading, SearchHold, SearchPlan, SearchSummary, SearchSummaryVersion, TitleVersion } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Section, Spinner } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatNumber } from '../../lib/format'
import { whenText } from '../../lib/when'
import { downloadOriginSymbol, downloadOriginText } from '../downloads/downloadText'
import { AUTOMATIC_TAB_PATH } from '../settings/tabs'
import { anchorText, indexerCodeText, loadCodeText, planReasonText, rejectionPhrase, seasonReasonText } from './automaticText'
import { SeasonPlanLines } from './SeasonPlanLines'
import { SeriesHolds } from './SeriesHolds'

type Props = {
  titleId: number
  titleYear: number | null
  plan: SearchPlan
  /** Laedt den Titel leise neu, etwa nach einer gestarteten Suche. */
  onRefresh: () => void
  /** Eine Serie (S5): statt Anker und Ergebnis je Fassung eine Zeile je Staffel. */
  series?: boolean
  /** Ein Album (Musik M5): wie ein Film, mit den Saetzen fuer Alben. */
  album?: boolean
  /** Oeffnet die Suche einer Staffel, fuer "Nur als Paket zu haben". */
  onSearchSeason?: (season: number) => void
  /** Eine Serie (S6): die Fassungen des Titels, fuer die Namen in den Saetzen zum Zurueckhalten. */
  versions?: readonly TitleVersion[]
  /** Eine Serie (S6): Fassungen, deren Serienordner gerade eingelesen wird. */
  reading?: readonly FolderReading[]
}

/**
 * Das Panel "Automatische Suche" auf der Titelseite eines Films (Schritt 3c, C8): zuletzt gesucht, naechste Suche mit
 * ihrem Grund, womit der Plan rechnet, und was die letzte Suche fand. "Jetzt automatisch suchen" gibt es nur, wenn der
 * Schalter an ist und eine Fassung etwas will; der Knopf laedt das beste passende Release, deshalb steht ein Satz direkt
 * darunter. Ist der Schalter aus, sagt das Panel es und fuehrt zur Automatik. Bei einer Serie (S5.9) zeigt es statt des
 * Ankers eine Zeile je Staffel, die etwas will, mit ihrem letzten Ergebnis.
 */
export function AutomaticSearch({ titleId, titleYear, plan, onRefresh, series = false, album = false, onSearchSeason, versions = [], reading = [] }: Props) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const notify = useNotice()
  const hintId = useId()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  // Das Ergebnis, das beim Klick galt. Solange es gilt, laeuft die gestartete Suche noch. undefined: nichts gestartet.
  const [startedWith, setStartedWith] = useState<string | null | undefined>(undefined)
  const on = plan.automatic === true
  const canSearchNow = on && plan.wanted === true
  const latest = plan.summary?.at ?? plan.last_at ?? null
  const waiting = startedWith !== undefined && startedWith === latest

  // Bis das Ergebnis da ist, fragt die Seite den Titel alle paar Sekunden neu ab, hoechstens drei Minuten lang.
  useEffect(() => {
    if (!waiting) return
    let asked = 0
    const timer = setInterval(() => {
      asked += 1
      if (asked > AUTOMATIC_POLL_MAX) {
        clearInterval(timer)
        setStartedWith(undefined)
        return
      }
      onRefresh()
    }, AUTOMATIC_POLL_MS)
    return () => clearInterval(timer)
  }, [waiting, onRefresh])

  async function searchNow() {
    if (busy) return
    setBusy(true)
    setProblem(null)
    try {
      await automaticApi.searchNow(titleId)
      setStartedWith(latest)
      notify(t('title.automatic.started'))
    } catch (error) {
      setProblem(error)
      // automatic_off oder nothing_wanted: Am Server gilt etwas anderes als auf der Seite. Sie zieht nach.
      if (error instanceof ApiError && error.status === 409) onRefresh()
    } finally {
      setBusy(false)
    }
  }

  const reason: string | null = typeof plan.reason === 'string' && plan.reason.length > 0 ? plan.reason : null
  // Dass die Automatik aus ist, steht schon oben. Der Grund "aus" steht dann nicht noch einmal da.
  const reasonText = (value: string) => (series ? seasonReasonText(t, value) : album && value === 'anchor' ? t('title.automatic.album.anchor') : planReasonText(t, value))
  const reasonShown = reason !== null && !(reason === 'off' && !on) ? reasonText(reason) : null
  const nextAt = typeof plan.next_at === 'string' && plan.next_at !== '' ? plan.next_at : null
  // S6: Haelt etwas eine Fassung zurueck, sagen die Saetze dazu, was gilt. Eine Zeile "Nächste Suche" daneben behauptete
  // etwas anderes, deshalb bleibt sie weg.
  const held: SearchHold[] = series && Array.isArray(plan.held) ? plan.held : []
  const showNext = (on || nextAt !== null || reasonShown !== null) && held.length === 0

  return (
    <Section
      title={t('title.automatic.title')}
      intro={series ? t('title.automatic.series.intro') : album ? t('title.automatic.album.intro') : t('title.automatic.intro')}
      actions={
        canSearchNow ? (
          // Der Satz steht direkt unter dem Knopf, am Telefon wie am grossen Bildschirm, und beschreibt ihn auch fuer Vorleseprogramme.
          <div className="flex flex-col items-start gap-1.5 sm:items-end">
            <Button onClick={() => void searchNow()} loading={busy} disabled={waiting} aria-describedby={hintId}>
              {!busy && <Symbol name="search" />}
              {t('title.automatic.searchNow')}
            </Button>
            <p id={hintId} className="max-w-64 text-xs text-mist-500 sm:text-right">
              {series ? t('title.automatic.series.searchNowHint') : t('title.automatic.searchNowHint')}
            </p>
          </div>
        ) : undefined
      }
    >
      {!on && (
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-mist-300">
          <Symbol name="info" className="h-4 w-4 shrink-0 text-info-500" />
          <span>{series ? t('title.automatic.series.off') : album ? t('title.automatic.album.off') : t('title.automatic.off')}</span>
          <Link to={AUTOMATIC_TAB_PATH} className="font-medium text-accent-400 hover:underline">
            {t('title.automatic.offLink')}
          </Link>
        </p>
      )}
      {waiting && (
        <p role="status" className="flex items-center gap-2 text-sm text-mist-300">
          <Spinner />
          {t('title.automatic.running')}
        </p>
      )}
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      {held.length > 0 && <SeriesHolds held={held} reading={reading} versions={versions} />}

      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
        <dt className="text-mist-500">{t('title.automatic.last')}</dt>
        <dd className="text-mist-200">{plan.last_at ? whenText(t, plan.last_at, language) : t('title.automatic.never')}</dd>
        {showNext && (
          <>
            <dt className="text-mist-500">{on ? t('title.automatic.next') : t('title.automatic.nextOff')}</dt>
            <dd className="flex min-w-0 flex-col gap-0.5">
              <span className="text-mist-200">{nextAt !== null ? whenText(t, nextAt, language) : t('title.automatic.nextNone')}</span>
              {reasonShown !== null && <span className="wrap-anywhere text-mist-400">{reasonShown}</span>}
            </dd>
          </>
        )}
        {!series && plan.anchor && (
          <>
            <dt className="text-mist-500">{t('title.automatic.anchor')}</dt>
            <dd className="wrap-anywhere text-mist-200">{anchorText(t, plan.anchor, language, titleYear)}</dd>
          </>
        )}
      </dl>

      {plan.summary && <Outcome summary={plan.summary} />}
      {series && <SeasonPlanLines seasons={Array.isArray(plan.seasons) ? plan.seasons : []} on={on} held={held.length > 0} onSearchSeason={onSearchSeason} />}
    </Section>
  )
}

/** Was die letzte automatische Suche fand: wie viele Releases, welche Indexer nicht geliefert haben, und je Fassung das beste Release. */
function Outcome({ summary }: { summary: SearchSummary }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const indexers = Array.isArray(summary.indexers) ? summary.indexers : []
  const versions = Array.isArray(summary.versions) ? summary.versions : []
  const failed = indexers.filter((indexer) => typeof indexer.code === 'string' && indexer.code !== '')
  const releases = typeof summary.releases === 'number' && Number.isFinite(summary.releases) ? summary.releases : 0
  const origin = downloadOriginText(t, summary.origin)
  const head = [
    typeof summary.at === 'string' && summary.at !== '' ? t('title.automatic.summary.at', { when: whenText(t, summary.at, language) }) : null,
    t('title.automatic.summary.releases', { count: releases, value: formatNumber(releases, language) }),
  ].filter((part): part is string => part !== null)

  return (
    <div className="flex flex-col gap-4 border-t border-ink-700 pt-4">
      <div className="flex flex-col gap-1.5">
        <h3 className="text-base font-semibold text-mist-100">{t('title.automatic.summary.title')}</h3>
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
          <p className="text-sm text-mist-300">{head.join(' ')}</p>
          {origin !== null && (
            <Badge>
              <Symbol name={downloadOriginSymbol(summary.origin)} className="h-3.5 w-3.5" />
              {origin}
            </Badge>
          )}
        </div>
      </div>

      {failed.length > 0 && (
        <div className="flex flex-col gap-2 rounded-xl border border-bad-500/40 bg-bad-500/10 p-3">
          <h4 className="flex items-center gap-2 text-sm font-semibold text-bad-500">
            <Symbol name="alert" className="h-4 w-4 shrink-0" />
            {t('search.indexers.failedTitle')}
          </h4>
          <ul aria-label={t('search.indexers.failedTitle')} className="flex flex-col gap-1.5 pl-6">
            {failed.map((indexer) => (
              <li key={indexer.id} className="flex min-w-0 flex-col text-sm">
                <span className="font-semibold wrap-anywhere text-mist-100">{indexer.name}</span>
                <span className="wrap-anywhere text-mist-300">{indexerCodeText(t, indexer.code ?? '')}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {versions.length > 0 && (
        <ul aria-label={t('title.automatic.summary.versionsLabel')} className="grid grid-cols-[minmax(0,1fr)] gap-3 lg:grid-cols-[repeat(2,minmax(0,1fr))]">
          {versions.map((version) => (
            <li key={version.version_id} className="min-w-0">
              <VersionResult version={version} />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * Das beste Release einer Fassung aus der letzten Suche, mit seinen ersten Gruenden als kurze Wendungen ohne Zahl. Wurde
 * es nicht geladen, sagt eine Zeile warum.
 */
function VersionResult({ version }: { version: SearchSummaryVersion }) {
  const { t } = useTranslation()
  const codes = Array.isArray(version.codes) ? version.codes.filter((code) => typeof code === 'string' && code !== '').slice(0, 5) : []
  const best = typeof version.best_title === 'string' && version.best_title !== '' ? version.best_title : null
  const loaded = version.loaded === true
  const loadCode = !loaded && typeof version.load_code === 'string' && version.load_code !== '' ? version.load_code : null

  return (
    <div role="group" aria-label={version.label} className="flex h-full min-w-0 flex-col gap-2.5 rounded-xl border border-ink-700 bg-ink-900/60 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h4 className="text-base font-semibold wrap-anywhere text-mist-100">{version.label}</h4>
        {loaded ? (
          <Badge tone="ok">
            <Symbol name="download" className="h-3.5 w-3.5" />
            {t('title.automatic.summary.loaded')}
          </Badge>
        ) : best === null ? (
          <Badge>{t('title.automatic.summary.nothing')}</Badge>
        ) : codes.length > 0 ? (
          <Badge tone="bad">
            <Symbol name="alert" className="h-3.5 w-3.5" />
            {t('checker.fitsNot')}
          </Badge>
        ) : (
          <Badge tone="ok">
            <Symbol name="check" className="h-3.5 w-3.5" />
            {t('checker.fits')}
          </Badge>
        )}
      </div>
      {best !== null ? (
        <div className="flex min-w-0 flex-col gap-0.5">
          <p className="text-xs text-mist-500">{t('title.automatic.summary.best')}</p>
          <p className="font-mono text-sm leading-5 wrap-anywhere text-mist-100">{best}</p>
        </div>
      ) : (
        <p className="text-sm text-mist-400">{t('title.automatic.summary.noRelease')}</p>
      )}
      {codes.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="text-xs font-semibold text-mist-400">{t('checker.reasonsTitle')}</p>
          <ul className="flex flex-col gap-1">
            {codes.map((code, index) => (
              <li key={`${code}-${index}`} className="flex items-start gap-2 text-sm text-mist-200">
                <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0 text-bad-500" />
                <span className="min-w-0 wrap-anywhere">{rejectionPhrase(t, code)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {loadCode !== null && <p className="text-sm wrap-anywhere text-mist-300">{t('title.automatic.summary.notLoaded', { reason: loadCodeText(t, loadCode, version.label) })}</p>}
    </div>
  )
}
