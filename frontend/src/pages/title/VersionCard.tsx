import { Fragment } from 'react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { EpisodeCounts, FolderReading, TitleDownload, TitleVersion, VersionCompanion, VersionLocation } from '../../api/types'
import { LaterButton } from '../../components/LaterButton'
import { Symbol } from '../../components/Symbol'
import { Button, ProgressBar } from '../../components/ui'
import { VersionChip } from '../../components/VersionChip'
import { formatCalendarDate, formatDateTime, formatNumber } from '../../lib/format'
import { mediaSummary } from '../../lib/media'
import { languageText } from '../../lib/names'
import { sizeText } from '../../lib/size'
import { percentOf } from '../../lib/states'
import { DOWNLOADS_PATH, DOWNLOADS_PROBLEMS_PATH, DOWNLOADS_WAITING_PATH } from '../downloads/address'
import { downloadStateText, isRunning, problemReasonText } from '../downloads/downloadText'
import { seriesOriginText, watchText } from './seriesText'
import { fileNameOf, isFromSource, originText, scoreUpgradeOf, subtitlesOf, subtitleText, targetText } from './versionDefinitions'

/** Warum ein Download haengt, in einem Satz. Die Codes kommen vom Server, unbekannte bekommen den allgemeinen Satz. */
function problemText(t: TFunction, code: string | null): string {
  switch (code) {
    case 'import_pending':
      return t('title.problem.importPending')
    case 'import_blocked':
      return t('title.problem.importBlocked')
    case 'download_error':
      return t('title.problem.downloadError')
    case 'download_warning':
      return t('title.problem.downloadWarning')
    default:
      return t('title.problem.unknown')
  }
}

const KNOWN_PROBLEMS = new Set(['import_pending', 'import_blocked', 'download_error', 'download_warning'])

/**
 * Der Satz zu "Besser möglich". Radarr sucht nur weiter, solange eine Verbindung die Fassung fuellt. Eine eigene oder
 * uebernommene Fassung sucht nexcrate seit 3c von selbst weiter, wenn die Automatik an ist; sonst verbessert man sie oben
 * ueber die Suche.
 */
function upgradeText(t: TFunction, version: TitleVersion, automatic: boolean, language: string): string {
  const from = version.quality ?? ''
  const to = targetText(version, language)
  if (isFromSource(version)) return to ? t('title.version.upgrade', { from, to }) : t('title.version.upgradeNoTarget', { from })
  // Decision 20: the quality fits already, the score does not yet. The sentence names both numbers instead of a target.
  const byScore = scoreUpgradeOf(version)
  if (byScore !== null) {
    const values = { from, count: byScore.score, score: formatNumber(byScore.score, language), until: formatNumber(byScore.until, language) }
    return automatic ? t('title.version.upgradeScoreAutomatic', values) : t('title.version.upgradeScore', values)
  }
  if (automatic) return to ? t('title.version.upgradeAutomatic', { from, to }) : t('title.version.upgradeAutomaticNoTarget', { from })
  return to ? t('title.version.upgradeOwn', { from, to }) : t('title.version.upgradeOwnNoTarget', { from })
}

/** Der Ort einer Fassung (Befund 13), oder null. Eine unbekannte Art oder ein leerer Pfad zaehlt wie keine Angabe. */
function locationOf(version: TitleVersion): VersionLocation | null {
  const location = version.location
  if (!location || typeof location.path !== 'string' || location.path === '') return null
  return location.kind === 'file' || location.kind === 'target' || location.kind === 'radarr' || location.kind === 'sonarr' ? location : null
}

function locationText(t: TFunction, location: VersionLocation, name: string): string {
  // Ohne Maskieren, damit der Pfad unveraendert im Satz steht und die Zeile ihn wiederfindet. React maskiert selbst.
  const values = { path: location.path, interpolation: { escapeValue: false } }
  switch (location.kind) {
    case 'file':
      return t('title.location.file', values)
    case 'target':
      return t('title.location.target', values)
    // Seit S1: der Serienordner, wie die Sonarr-Verbindung ihn sieht.
    case 'sonarr':
      return t('series.location', { ...values, name })
    default:
      return t('title.location.radarr', values)
  }
}

/**
 * The quality with where it came from (library from disk, decision 22): the measured resolution, the name, or Radarr.
 * Without `quality_from`, as from an older server, the quality stands alone.
 */
function qualityText(t: TFunction, version: TitleVersion): string | null {
  const quality = version.quality
  if (!quality) return null
  switch (version.quality_from) {
    case 'media':
      return t('title.version.qualityFrom.media', { quality })
    case 'name':
      return t('title.version.qualityFrom.name', { quality })
    case 'radarr':
      return t('title.version.qualityFrom.radarr', { quality })
    default:
      return quality
  }
}

/**
 * The sentence about the version's `release.nex` (library from disk, L1 "Writing"). `current` and `written` say when
 * it was written; every other state says why there is none or why nexcrate leaves the file alone. null without a
 * companion, for a version a source feeds or one never looked at.
 */
function companionText(t: TFunction, companion: VersionCompanion | null | undefined, language: string): string | null {
  if (!companion || typeof companion !== 'object' || typeof companion.state !== 'string') return null
  switch (companion.state) {
    case 'current':
    case 'written':
      return companion.written_at ? t('title.version.companion.written', { time: formatDateTime(companion.written_at, language) }) : t('title.version.companion.writtenNoTime')
    case 'not_writable':
      return t('title.version.companion.not_writable')
    case 'foreign':
      return t('title.version.companion.foreign')
    case 'changed':
      return t('title.version.companion.changed')
    case 'broken':
    case 'newer_format':
    case 'other_installation':
      return t('title.version.companion.other_file')
    case 'file_missing':
      return t('title.version.companion.file_missing')
    case 'folder_missing':
      return t('title.version.companion.folder_missing')
    case 'no_space':
      return t('title.version.companion.no_space')
    case 'failed':
      return t('title.version.companion.failed')
    case 'missing':
    case 'outdated':
      return t('title.version.companion.missing')
    default:
      return null
  }
}

/** The companion states that are a problem for the owner: the file is not there and nexcrate cannot change that alone. */
const COMPANION_TROUBLE = new Set(['not_writable', 'no_space', 'failed', 'folder_missing', 'file_missing'])

/**
 * Wo die Datei liegt, wohin die naechste kommt oder wo Radarr den Film fuehrt, in einer Zeile. Der Pfad steht in
 * Schreibmaschinenschrift und bricht an jeder Stelle um. Findet sich der Pfad im Satz nicht, steht der Satz schlicht da.
 */
function LocationLine({ location, name }: { location: VersionLocation; name: string }) {
  const { t } = useTranslation()
  const text = locationText(t, location, name)
  const at = text.indexOf(location.path)
  return (
    <p className="flex items-start gap-2 text-sm text-mist-400">
      <Symbol name="folder" className="mt-0.5 h-4 w-4 shrink-0 text-accent-400" />
      <span className="min-w-0 wrap-anywhere">
        {at < 0 ? (
          text
        ) : (
          <>
            {text.slice(0, at)}
            <span className="font-mono text-xs text-mist-300">{location.path}</span>
            {text.slice(at + location.path.length)}
          </>
        )}
      </span>
    </p>
  )
}

/**
 * Eine Fassung mit Herkunft, Ort, Profil, Datei und Zustand. Hinzufuegen und entfernen
 * geht ueber "Fassungen ändern" oben auf der Seite. Laedt nexcrate seit Schritt 3 selbst fuer die
 * Fassung, steht der Download mit Fortschritt und dem Weg zu den Downloads darunter. Seit Schritt 3c
 * stehen unter der Datei die Untertitel, die nexcrate daneben gelegt hat.
 *
 * `automatic` ist der Schalter aus dem `search_plan` des Titels. Ist er an, sagen eigene Fassungen, dass nexcrate von
 * selbst sucht. Ohne Plan (ein Server von davor) gilt er als aus.
 *
 * Seit S1 zeigt die Fassung einer Serie (mit `watch`) statt des Zustandssatzes, was sie ueberwacht und wie viele Folgen
 * da sind. `onChangeWatch` oeffnet "Überwachen ändern"; eine Fassung aus Sonarr bekommt den Knopf nicht.
 *
 * Seit S6 hat eine eigene Serienfassung mit Serienordner "Ordner neu einlesen" (`onReadFolder`). Laeuft eine Einlesung,
 * steht ihr Fortschritt statt des Knopfes da (`reading`).
 */
export function VersionCard({
  version,
  automatic = false,
  onChangeWatch,
  onChangeMonitored,
  monitorBusy = false,
  onReadFolder,
  reading = null,
  readBusy = false,
  onDeleteFiles,
}: {
  version: TitleVersion
  automatic?: boolean
  onChangeWatch?: (version: TitleVersion) => void
  /** Fuer Filme: beobachten oder in Ruhe lassen. Ohne Handler gibt es den Knopf nicht. */
  onChangeMonitored?: (version: TitleVersion, monitored: boolean) => void
  monitorBusy?: boolean
  /** S6: liest den Serienordner dieser Fassung neu ein. Fehlt der Serienordner, gibt es den Knopf nicht. */
  onReadFolder?: (version: TitleVersion) => void
  /** S6: laeuft gerade eine Einlesung fuer diese Fassung? */
  reading?: FolderReading | null
  readBusy?: boolean
  /** /api/v1 V2: die Dateien dieser Fassung in den Papierkorb. Nur fuer eigene Fassungen mit Datei. */
  onDeleteFiles?: (version: TitleVersion) => void
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const series = version.watch !== null && version.watch !== undefined
  const fed = version.watch?.fed === true
  const watching = watchText(t, version)
  const fromSource = isFromSource(version)
  const watched = version.monitored !== false
  const failure = version.last_failure ?? null
  const waitingFor = version.waiting ?? null
  const download = version.download ?? null
  // Der Zustand folgt dem eigenen Download. Dann sagt die Karte des Downloads, was los ist, nicht der Satz aus Radarr.
  const followsDownload = download !== null && (version.state === 'downloading' || version.state === 'problem')
  const location = locationOf(version)
  const subtitles = subtitlesOf(version)
  // Library from disk: the stored media data in one line, and the state of the release.nex.
  const media = mediaSummary(version.media, language, t('title.version.bit'))
  const companion = companionText(t, version.companion, language)
  const companionTrouble = typeof version.companion?.state === 'string' && COMPANION_TROUBLE.has(version.companion.state)
  // S6: Einlesen geht nur, wenn die Fassung einen Serienordner hat. `file` heisst genau das.
  const canRead = series && !fed && location?.kind === 'file'
  // Loeschen geht nur bei eigenen Fassungen, die eine Datei haben; eine aus Radarr oder Sonarr loescht man dort.
  const canDelete = onDeleteFiles !== undefined && !fromSource && !fed && (version.size_bytes ?? 0) > 0

  const rows: { label: string; value: string | null; mono?: boolean }[] = [
    // Seit S2 haben auch eigene Serienfassungen ein Profil.
    { label: t('title.version.profile'), value: version.profile_name || null },
    // Mit einem Ort sagt die Zeile oben genauer, wo die Fassung liegt. Der Stammordner stuende sonst doppelt da.
    { label: t('title.version.folder'), value: location === null ? version.root_folder || null : null, mono: true },
    { label: t('title.version.quality'), value: qualityText(t, version) },
    // A group of qualities shows its qualities, not its inner name (decision 20).
    { label: t('title.version.upgradeTo'), value: version.state === 'upgrade' ? targetText(version, language) : null },
    // Ohne Datei schickt der Server 0. "0,0 GB" waere nur Rauschen.
    { label: t('title.version.size'), value: version.size_bytes ? sizeText(t, version.size_bytes, language) : null },
    { label: t('title.version.languages'), value: version.languages.length > 0 ? version.languages.map((name) => languageText(t, name)).join(', ') : null },
    { label: t('title.version.releaseGroup'), value: version.release_group },
    // Bei einer Serie ist das der Serienordner; er steht schon in der Zeile zum Ort.
    { label: t('title.version.path'), value: series ? null : version.relative_path, mono: true },
  ].filter((row) => row.value)

  return (
    <article className="flex min-w-0 flex-col gap-4 rounded-2xl border border-ink-700 bg-ink-900/60 p-5">
      <div className="flex flex-col gap-1.5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="flex min-w-0 items-center gap-2 text-xl font-bold wrap-anywhere">
            <Symbol name="layers" className="h-5 w-5 shrink-0 text-accent-400" />
            {version.label}
          </h3>
          <VersionChip version={version} />
        </div>
        <p className="flex items-start gap-2 text-sm text-mist-400">
          <Symbol name={fromSource ? 'import' : 'sparkle'} className="mt-0.5 h-4 w-4 shrink-0 text-accent-400" />
          <span className="min-w-0 wrap-anywhere">{series ? seriesOriginText(t, version) : originText(t, version)}</span>
        </p>
        {/* /api/v1 V2: ein Programm hat diese Fassung angefragt. Der Name ist der seines Schluessels. */}
        {version.origin_key && (
          <p className="flex items-start gap-2 text-sm text-mist-400">
            <Symbol name="link" className="mt-0.5 h-4 w-4 shrink-0 text-accent-400" />
            <span className="flex min-w-0 flex-col wrap-anywhere">
              <span>{t('title.origin.program', { key: version.origin_key })}</span>
              {version.origin && <span className="font-mono text-xs leading-5 text-mist-500">{t('title.origin.programRef', { origin: version.origin })}</span>}
            </span>
          </p>
        )}
        {watching !== null && (
          <p className="flex items-start gap-2 text-sm text-mist-400">
            <Symbol name="eye" className="mt-0.5 h-4 w-4 shrink-0 text-accent-400" />
            <span className="min-w-0 wrap-anywhere">{watching}</span>
          </p>
        )}
        {location !== null && <LocationLine location={location} name={version.source_name ?? ''} />}
        {/* Rueckmeldung 20.09.2026: Ein fehlgeschlagener Download stand nur im Verlauf. Er bleibt hier stehen, bis
            er unter Downloads erledigt ist; sein Release ist gesperrt. */}
        {waitingFor !== null && (
          <p className="flex items-start gap-2 text-sm text-mist-400">
            <Symbol name="clock" className="mt-0.5 h-4 w-4 shrink-0 text-accent-400" />
            <span className="min-w-0 wrap-anywhere">
              {waitingFor.due === true
                ? t('title.version.waitingDue', { count: waitingFor.count, value: formatNumber(waitingFor.count, language) })
                : t('title.version.waiting', { count: waitingFor.count, value: formatNumber(waitingFor.count, language), date: formatDateTime(waitingFor.due_at, language) })}{' '}
              <Link to={DOWNLOADS_WAITING_PATH} className="text-accent-400 hover:underline">
                {t('title.version.waitingLink')}
              </Link>
            </span>
          </p>
        )}
        {failure !== null && (
          <p className="flex items-start gap-2 text-sm text-bad-500">
            <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
            <span className="min-w-0 wrap-anywhere">{t('title.version.lastFailure', { date: formatDateTime(failure.at, language) })}</span>
          </p>
        )}
      </div>

      {(rows.length > 0 || subtitles.length > 0) && (
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
          {rows.map((row) => (
            <Fragment key={row.label}>
              <dt className="text-mist-500">{row.label}</dt>
              <dd className={'wrap-anywhere ' + (row.mono ? 'font-mono text-xs leading-5 text-mist-300' : 'text-mist-200')}>{row.value}</dd>
            </Fragment>
          ))}
          {subtitles.length > 0 && (
            <>
              <dt className="text-mist-500">{t('title.version.subtitles')}</dt>
              <dd className="min-w-0">
                <ul aria-label={t('title.version.subtitlesLabel', { label: version.label })} className="flex flex-col gap-1.5">
                  {subtitles.map((subtitle, index) => {
                    const name = fileNameOf(subtitle.file)
                    return (
                      <li key={`${subtitle.file}-${index}`} className="flex min-w-0 flex-col">
                        <span className="text-mist-200">{subtitleText(t, subtitle, language)}</span>
                        {name !== null && <span className="font-mono text-xs leading-5 wrap-anywhere text-mist-500">{name}</span>}
                      </li>
                    )
                  })}
                </ul>
              </dd>
            </>
          )}
        </dl>
      )}

      {media !== null && <p className="text-sm wrap-anywhere text-mist-400">{t('title.version.media', { summary: media })}</p>}
      {companion !== null && (
        <p className={'flex items-start gap-2 text-sm ' + (companionTrouble ? 'text-bad-500' : 'text-mist-400')}>
          <Symbol name={companionTrouble ? 'alert' : 'folder'} className="mt-0.5 h-4 w-4 shrink-0" />
          <span className="min-w-0 wrap-anywhere">{companion}</span>
        </p>
      )}

      {/* Eine Serie sagt mit Zahlen, was da ist. Die Saetze des Films (gesucht, geht besser) passen nicht auf Folgen. */}
      {series ? version.counts && <SeriesCounts counts={version.counts} /> : !followsDownload && <StateNote version={version} automatic={automatic} />}
      {/* Seit der Durchsicht von S4: haengt eine Folge an einem Download, der den Besitzer braucht, fuehrt die Karte dorthin. */}
      {series && !fed && version.state === 'problem' && (
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-bad-400">
          <Symbol name="alert" className="h-4 w-4 shrink-0" />
          <span>{t('series.version.problemLine')}</span>
          <Link to={DOWNLOADS_PROBLEMS_PATH} className="font-medium text-accent-400 hover:underline">
            {t('series.version.toProblems')}
          </Link>
        </p>
      )}
      {download !== null && <DownloadNote label={version.label} download={download} />}

      {/* Seit S6: der Serienordner wird eingelesen. Bis das durch ist, will die Fassung nichts. */}
      {series && !fed && reading !== null && <ReadingNote reading={reading} />}

      {/* Gesucht wird je Titel oben auf der Seite, fuer alle Fassungen zugleich. Deshalb hier kein eigener Suchknopf mehr. */}
      {series ? (
        !fed &&
        (onChangeWatch || canRead || canDelete) && (
          <div className="mt-auto flex flex-col gap-1.5">
            <div className="flex flex-wrap gap-2">
              {onChangeWatch && (
                <Button size="sm" variant="ghost" aria-label={t('series.watch.changeLabel', { label: version.label })} onClick={() => onChangeWatch(version)}>
                  <Symbol name="eye" />
                  {t('series.watch.change')}
                </Button>
              )}
              {canRead && onReadFolder && (
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={t('series.read.actionLabel', { label: version.label })}
                  loading={readBusy}
                  disabled={readBusy || reading !== null}
                  onClick={() => onReadFolder(version)}
                >
                  <Symbol name="refresh" />
                  {t('series.read.action')}
                </Button>
              )}
              {canDelete && (
                <Button size="sm" variant="ghost" aria-label={t('title.files.deleteAllLabel', { label: version.label })} onClick={() => onDeleteFiles(version)}>
                  <Symbol name="trash" />
                  {t('title.files.deleteAll')}
                </Button>
              )}
            </div>
            {canRead && onReadFolder && <p className="text-xs text-mist-500">{t('series.read.hint')}</p>}
          </div>
        )
      ) : (
        <div className="mt-auto flex flex-wrap gap-2">
          {/* Rueckmeldung 20.09.2026: eine einzelne Fassung in Ruhe lassen, ohne den ganzen Titel zu entfernen.
              Eine Fassung aus Radarr wird dort beobachtet. */}
          {!fromSource && onChangeMonitored && (
            <Button
              size="sm"
              variant="ghost"
              loading={monitorBusy}
              aria-label={t(watched ? 'title.version.stopWatchingLabel' : 'title.version.watchLabel', { label: version.label })}
              onClick={() => onChangeMonitored(version, !watched)}
            >
              <Symbol name={watched ? 'eyeOff' : 'eye'} />
              {t(watched ? 'title.version.stopWatching' : 'title.version.watch')}
            </Button>
          )}
          {canDelete && (
            <Button size="sm" variant="ghost" aria-label={t('title.files.deleteLabel', { label: version.label })} onClick={() => onDeleteFiles(version)}>
              <Symbol name="trash" />
              {t('title.files.delete')}
            </Button>
          )}
          <LaterButton size="sm" label={t('title.version.editLabel', { label: version.label })}>
            <Symbol name="settings" />
            {t('common.actions.edit')}
          </LaterButton>
        </div>
      )}
    </article>
  )
}

/**
 * Die Zahlen einer Serienfassung: wie viele gelaufene, ueberwachte Folgen da sind, wie viele noch kommen und wann die
 * naechste laeuft. Ohne gelaufene Folge sagt ein Satz das, statt "0 von 0".
 */
function SeriesCounts({ counts }: { counts: EpisodeCounts }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const aired = counts.aired_watched
  const coming = Math.max(0, counts.watched - counts.aired_watched)
  const upgrade = counts.upgrade ?? 0
  const specialsMissing = counts.specials_missing ?? 0
  const main = aired > 0 ? t('series.counts.have', { have: formatNumber(counts.have, language), total: formatNumber(aired, language) }) : t('series.counts.none')
  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm text-mist-200 tabular-nums">{main}</p>
      {aired > 0 && <ProgressBar value={counts.have / aired} tone="ok" label={main} />}
      {coming > 0 && <p className="text-xs text-mist-500 tabular-nums">{t('series.counts.coming', { count: coming, value: formatNumber(coming, language) })}</p>}
      {upgrade > 0 && (
        <p className="flex items-center gap-1.5 text-xs text-accent-400 tabular-nums">
          <Symbol name="arrowUp" className="h-3.5 w-3.5 shrink-0" />
          {t('series.counts.upgrade', { count: upgrade, value: formatNumber(upgrade, language) })}
        </p>
      )}
      {specialsMissing > 0 && (
        <p className="text-xs text-mist-500 tabular-nums">{t('series.counts.specialsMissing', { count: specialsMissing, value: formatNumber(specialsMissing, language) })}</p>
      )}
      {counts.next_air_date && <p className="text-xs text-mist-500 tabular-nums">{t('series.counts.next', { date: formatCalendarDate(counts.next_air_date, language) })}</p>}
    </div>
  )
}

/**
 * Der Serienordner dieser Fassung wird eingelesen (S6, P1): vorgemerkt, oder mit Zahlen und Balken. Bis das durch ist,
 * sucht nexcrate fuer die Fassung nichts.
 */
function ReadingNote({ reading }: { reading: FolderReading }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const total = typeof reading.total === 'number' && Number.isFinite(reading.total) && reading.total > 0 ? reading.total : null
  const done = typeof reading.done === 'number' && Number.isFinite(reading.done) && reading.done > 0 ? reading.done : 0
  const progress = total !== null ? t('series.read.progress', { done: formatNumber(done, language), total: formatNumber(total, language) }) : null

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-accent-500/40 bg-accent-500/5 p-3" role="status">
      <p className="flex items-start gap-2 text-sm text-mist-200">
        <Symbol name="folder" className="mt-0.5 h-4 w-4 shrink-0 text-accent-400" />
        <span className="min-w-0 wrap-anywhere">{reading.state === 'queued' ? t('series.read.queued') : t('series.read.running')}</span>
      </p>
      {progress !== null && (
        <>
          <ProgressBar value={done / (total ?? 1)} tone="accent" label={progress} />
          <p className="text-xs text-mist-500 tabular-nums">{progress}</p>
        </>
      )}
    </div>
  )
}

/** Der Download, den nexcrate fuer diese Fassung selbst laedt: Zustand, Fortschritt, Problem und der Weg zu den Downloads. */
function DownloadNote({ label, download }: { label: string; download: TitleDownload }) {
  const { t } = useTranslation()
  const urgent = download.state === 'problem'
  const percent = isRunning(download.state) && download.progress !== null ? percentOf(download.progress) : null
  const client = download.client_name ?? null

  return (
    <div className={'flex flex-col gap-2 rounded-xl border p-3 text-sm ' + (urgent ? 'border-bad-500/40 bg-bad-500/10' : 'border-info-500/30 bg-info-500/5')}>
      <p className={'flex items-center gap-2 font-medium ' + (urgent ? 'text-bad-500' : 'text-info-500')}>
        <Symbol name={urgent ? 'alert' : 'download'} className="h-4 w-4 shrink-0" />
        {downloadStateText(t, download.state)}
      </p>
      {client !== null && (
        <p className="flex min-w-0 items-center gap-1.5 text-xs text-mist-400">
          <Symbol name="server" className="h-3.5 w-3.5 shrink-0" />
          <span className="min-w-0 wrap-anywhere">{t('title.download.client', { name: client })}</span>
        </p>
      )}
      {percent !== null && (
        <>
          <ProgressBar value={percent / 100} label={t('title.download.progressLabel', { label })} />
          <p className="text-xs text-mist-400">{t('title.download.progress', { percent })}</p>
        </>
      )}
      {download.problem_code !== null && <p className="wrap-anywhere text-mist-300">{problemReasonText(t, download.problem_code, client ?? t('downloads.someClient'))}</p>}
      <Link to={urgent ? DOWNLOADS_PROBLEMS_PATH : DOWNLOADS_PATH} className="w-fit font-medium text-accent-400 hover:underline">
        {urgent ? t('title.download.linkProblems') : t('title.download.link')}
      </Link>
    </div>
  )
}

function StateNote({ version, automatic }: { version: TitleVersion; automatic: boolean }) {
  const { t, i18n } = useTranslation()

  switch (version.state) {
    case 'downloading': {
      const percent = percentOf(version.progress)
      return (
        <div className="flex flex-col gap-2 rounded-xl border border-info-500/30 bg-info-500/5 p-3">
          <ProgressBar value={percent / 100} label={t('title.version.downloading', { percent })} />
          <p className="text-sm text-info-500">{t('title.version.downloading', { percent })}</p>
        </div>
      )
    }
    case 'problem':
      return (
        <div className="flex flex-col gap-1.5 rounded-xl border border-bad-500/40 bg-bad-500/10 p-3 text-sm">
          <p className="flex items-start gap-2 text-bad-500">
            <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{problemText(t, version.problem_code)}</span>
          </p>
          {version.problem_code && !KNOWN_PROBLEMS.has(version.problem_code) && (
            <p className="pl-6 font-mono text-xs wrap-anywhere text-mist-500">{t('title.version.problemCode', { code: version.problem_code })}</p>
          )}
        </div>
      )
    case 'upgrade':
      return (
        <p className="flex items-start gap-2 rounded-xl border border-accent-500/40 bg-accent-500/5 p-3 text-sm text-mist-200">
          <Symbol name="arrowUp" className="mt-0.5 h-4 w-4 shrink-0 text-accent-400" />
          <span>{upgradeText(t, version, automatic, i18n.language)}</span>
        </p>
      )
    case 'wanted':
      return (
        <p className="flex items-start gap-2 rounded-xl border border-dashed border-ink-600 p-3 text-sm text-mist-400">
          <Symbol name="clock" className="mt-0.5 h-4 w-4 shrink-0" />
          {/* Radarr sucht nur, was eine Verbindung fuellt. Eine eigene Fassung sucht nexcrate von selbst, sobald die Automatik an ist. */}
          <span>{isFromSource(version) ? t('title.version.wanted') : automatic ? t('title.version.wantedAutomatic') : t('title.version.wantedOwn')}</span>
        </p>
      )
    case 'unmonitored':
      return (
        <p className="flex items-start gap-2 rounded-xl border border-ink-600 p-3 text-sm text-mist-400">
          <Symbol name="eyeOff" className="mt-0.5 h-4 w-4 shrink-0" />
          {/* Wie bei `wanted`: Ohne Verbindung sucht Radarr nicht, der Satz darf es nicht behaupten. */}
          <span>{isFromSource(version) ? t('title.version.unmonitored') : t('title.version.unmonitoredOwn')}</span>
        </p>
      )
    case 'available':
      return (
        <p className="flex items-center gap-2 text-sm text-ok-500">
          <Symbol name="check" className="h-4 w-4 shrink-0" />
          {t('title.version.available')}
        </p>
      )
  }
}
