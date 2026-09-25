/**
 * Die Saetze zu Downloads. Der Server schickt Zustaende und Probleme als Codes mit Werten, nie als
 * Saetze; hier werden sie zu Text. Die Schluessel stehen woertlich da, damit `keys.test.ts` sie sieht.
 */

import type { TFunction } from 'i18next'

import type { Download, DownloadAftermath, DownloadProblem, DownloadScope, DownloadTransfer, PathMapping } from '../../api/types'
import type { SymbolName } from '../../components/Symbol'
import { formatDateTime, formatNumber } from '../../lib/format'
import { countingName } from '../checker/seriesCheckerText'

/**
 * Woher ein Download kam, als kleine Marke (Schritt 3c): eine geplante Suche oder "Jetzt automatisch suchen", RSS oder ein
 * Ersatz nach einem Fehlschlag. Von Hand geladene, unbekannte und die eines Servers von davor bekommen keine.
 */
export function downloadOriginText(t: TFunction, origin: string | null | undefined): string | null {
  switch (origin) {
    case 'search':
      return t('downloads.origin.search')
    case 'rss':
      return t('downloads.origin.rss')
    case 'replacement':
      return t('downloads.origin.replacement')
    default:
      return null
  }
}

export function downloadOriginSymbol(origin: string | null | undefined): SymbolName {
  switch (origin) {
    case 'rss':
      return 'refresh'
    case 'replacement':
      return 'swap'
    default:
      return 'search'
  }
}

export type DownloadTone = 'neutral' | 'ok' | 'info' | 'bad'

/** Die Probleme mit eigenem Satz, wie im Plan unter "Downloads". `encrypted` kam mit dem Entpacken dazu. */
export const PROBLEM_CODES = [
  'path_not_found',
  'packed',
  'no_video',
  'no_space',
  'gone_from_client',
  'client_error',
  'import_failed',
  'dangerous_file',
  'encrypted',
  'client_unreachable',
  'stalled',
  // S4: Serien und Filme mit mehreren Videos.
  'files_unassigned',
  'other_series_suspected',
  'several_videos',
  'multi_part',
  'import_stalled',
  'too_many_files',
  // M4: Alben.
  'no_audio',
  'album_single_file',
  'album_not_better',
  'album_tracks_missing',
  // Rueckmeldung 20.09.2026: Ein fehlgeschlagener Download wartet hier, bis er erledigt ist.
  'download_failed',
  // 24.09.2026: ein abgeschnittenes Video, nie abgelegt.
  'file_truncated',
] as const

export function isKnownProblem(code: string): boolean {
  return (PROBLEM_CODES as readonly string[]).includes(code)
}

/** Der Zustand in einem Wort. Waehrend des Ablegens sagt `step` genauer, was passiert, etwa "Wird entpackt". */
export function downloadStateText(t: TFunction, state: string, step: string | null = null): string {
  switch (state) {
    case 'queued':
      return t('downloads.state.queued')
    case 'downloading':
      return t('downloads.state.downloading')
    case 'paused':
      return t('downloads.state.paused')
    case 'completed':
      // Seit M4: ein Albendownload wartet auf die Titellisten seines Albums.
      return step === 'waiting_tracks' ? t('downloads.state.waitingTracks') : t('downloads.state.completed')
    case 'importing':
      switch (step) {
        case 'unpacking':
          return t('downloads.state.unpacking')
        case 'matching':
          return t('downloads.state.matching')
        case 'fingerprinting':
          return t('downloads.state.fingerprinting')
        case 'filing':
          return t('downloads.state.filing')
        default:
          return t('downloads.state.importing')
      }
    case 'imported':
      return t('downloads.state.imported')
    case 'failed':
      return t('downloads.state.failed')
    case 'problem':
      return t('downloads.state.problem')
    case 'removed':
      return t('downloads.state.removed')
    default:
      return t('downloads.state.unknown', { state })
  }
}

/** Blau laeuft, Gruen abgelegt, Rosa braucht Aufmerksamkeit, sonst grau. Wie die Zustaende in `lib/states.ts`. */
export function stateTone(state: string): DownloadTone {
  switch (state) {
    case 'downloading':
    case 'completed':
    case 'importing':
      return 'info'
    case 'imported':
      return 'ok'
    case 'failed':
    case 'problem':
      return 'bad'
    default:
      return 'neutral'
  }
}

/** Laeuft noch im Programm. Nur dann bedeuten Fortschritt und Restzeit etwas. */
export function isRunning(state: string): boolean {
  return state === 'queued' || state === 'downloading' || state === 'paused'
}

/** Nur fuer diese beiden Zustaende nimmt der Server "erneut versuchen" an. */
export function isRetryable(state: string): boolean {
  return state === 'problem' || state === 'completed'
}

/** Der Name des Programms, oder "das Download-Programm", wenn der Download keines mehr hat. */
export function clientNameOf(t: TFunction, download: Pick<Download, 'client'>): string {
  return download.client?.name ?? t('downloads.someClient')
}

/** Was passiert ist, in einem Satz. Ein unbekannter Code bekommt den allgemeinen Satz. */
export function problemReasonText(t: TFunction, code: string, client: string, values: Record<string, unknown> = {}): string {
  switch (code) {
    case 'download_failed': {
      const reason = typeof values.reason === 'string' ? values.reason : null
      return failedReasonText(t, reason, client) ?? t('downloads.problems.reason.download_failed', { client })
    }
    case 'path_not_found':
      return t('downloads.problems.reason.path_not_found', { client })
    case 'packed':
      return t('downloads.problems.reason.packed')
    case 'no_video':
      return t('downloads.problems.reason.no_video')
    case 'file_truncated':
      // Bei einer Serie nennt der Server die Folge.
      return typeof values.episodes === 'string' && values.episodes !== ''
        ? t('downloads.problems.reason.file_truncatedEpisodes', { episodes: values.episodes })
        : t('downloads.problems.reason.file_truncated')
    case 'no_space':
      return t('downloads.problems.reason.no_space')
    case 'gone_from_client':
      return t('downloads.problems.reason.gone_from_client', { client })
    case 'client_error':
      return t('downloads.problems.reason.client_error', { client })
    case 'import_failed':
      return t('downloads.problems.reason.import_failed')
    case 'dangerous_file':
      return t('downloads.problems.reason.dangerous_file')
    case 'encrypted':
      return t('downloads.problems.reason.encrypted')
    case 'client_unreachable':
      return t('downloads.problems.reason.client_unreachable', { client })
    case 'stalled':
      return t('downloads.problems.reason.stalled')
    case 'files_unassigned':
      return t('downloads.problems.reason.files_unassigned')
    case 'other_series_suspected':
      return t('downloads.problems.reason.other_series_suspected')
    case 'several_videos':
      return t('downloads.problems.reason.several_videos')
    case 'multi_part':
      return t('downloads.problems.reason.multi_part')
    case 'import_stalled':
      return t('downloads.problems.reason.import_stalled')
    case 'too_many_files':
      return t('downloads.problems.reason.too_many_files')
    case 'no_audio':
      return t('downloads.problems.reason.no_audio')
    case 'album_single_file':
      return t('downloads.problems.reason.album_single_file')
    case 'album_not_better':
      return t('downloads.problems.reason.album_not_better')
    case 'album_tracks_missing':
      return t('downloads.problems.reason.album_tracks_missing')
    default:
      return t('downloads.problems.reason.unknown')
  }
}

function countOf(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

/** Die vorgeschlagene Zuordnung eines `path_not_found`, wenn sie lesbar ist. Sonst null. */
export function proposalOf(problem: DownloadProblem | null): PathMapping | null {
  if (problem === null || problem.code !== 'path_not_found') return null
  const value = problem.values?.proposal
  if (!value || typeof value !== 'object') return null
  const { remote, local } = value as Record<string, unknown>
  return typeof remote === 'string' && typeof local === 'string' && remote !== '' && local !== '' ? { remote, local } : null
}

/**
 * Warum sich ein gepackter Download nicht entpacken liess, nach `values.reason`. Ohne Grund, oder mit einem, den
 * diese Oberflaeche nicht kennt, der allgemeine Satz von vor dem Entpacken.
 */
function packedWhyText(t: TFunction, reason: unknown): string {
  switch (reason) {
    case 'unsupported':
      return t('downloads.problems.why.packedReason.unsupported')
    case 'incomplete':
      return t('downloads.problems.why.packedReason.incomplete')
    case 'broken':
      return t('downloads.problems.why.packedReason.broken')
    case 'unsafe':
      return t('downloads.problems.why.packedReason.unsafe')
    case 'nested':
      return t('downloads.problems.why.packedReason.nested')
    case 'too_large':
      return t('downloads.problems.why.packedReason.too_large')
    default:
      return t('downloads.problems.why.packed')
  }
}

/** Warum, und was als Naechstes hilft. */
export function problemWhyText(t: TFunction, problem: DownloadProblem, client: string, album = false): string {
  switch (problem.code) {
    case 'path_not_found':
      return proposalOf(problem) !== null ? t('downloads.problems.why.pathProposal', { client }) : t('downloads.problems.why.pathNoProposal')
    case 'packed':
      return packedWhyText(t, problem.values?.reason)
    case 'no_video':
      return t('downloads.problems.why.no_video')
    case 'file_truncated':
      return t('downloads.problems.why.file_truncated')
    case 'no_space':
      return t('downloads.problems.why.no_space')
    case 'gone_from_client':
      return t('downloads.problems.why.gone_from_client')
    case 'client_error':
      return t('downloads.problems.why.client_error', { client })
    case 'import_failed':
      return importFailedWhyText(t, problem.values ?? {})
    case 'download_failed': {
      // Seit dem 22.09.2026 nur noch, wenn sich niemand kuemmert: drei Ersatzversuche heute, die Automatik aus.
      const said = failedDetailText(t, typeof problem.values?.detail === 'string' ? problem.values.detail : null, client)
      const next = t('downloads.problems.why.download_failed')
      return said !== null ? `${said} ${next}` : next
    }
    case 'dangerous_file':
      return t('downloads.problems.why.dangerous_file')
    case 'encrypted':
      return t('downloads.problems.why.encrypted')
    case 'client_unreachable':
      return t('downloads.problems.why.client_unreachable')
    case 'stalled':
      return t('downloads.problems.why.stalled')
    case 'files_unassigned': {
      if (album) {
        // Seit M4: Dateien eines Albums, die nexcrate keinem Titel sicher zuordnen konnte.
        const filed = countOf(problem.values?.filed)
        const open = countOf(problem.values?.open)
        const missing = countOf(problem.values?.missing)
        return t('downloads.problems.why.albumUnassigned', {
          filed: t('downloads.problems.why.albumFiledPart', { count: filed, value: String(filed) }),
          open: t('downloads.problems.why.albumOpenPart', { count: open, value: String(open) }),
          missing: t('downloads.problems.why.albumMissingPart', { count: missing, value: String(missing) }),
        })
      }
      // Seit der Durchsicht: ein Paket, das anders zaehlt als TMDB, legt nichts von selbst ab und sagt, welche Nummern fehlen.
      const unknown = typeof problem.values?.unknown === 'string' ? problem.values.unknown : ''
      if (unknown !== '') return t('downloads.problems.why.unknownNumbers', { codes: unknown })
      // Seit dem Durchlauf ab null: Dateien, die anders zaehlen als beim Laden angenommen, wurden danach gelesen.
      const counted = typeof problem.values?.counted === 'string' ? problem.values.counted : ''
      if (counted !== '') return t('downloads.problems.why.countedOtherwise', { scheme: countingName(t, counted) })
      const filed = countOf(problem.values?.filed)
      const open = countOf(problem.values?.open)
      return t('downloads.problems.why.files_unassigned', {
        filed: t('downloads.problems.why.filedPart', { count: filed, value: String(filed) }),
        open: t('downloads.problems.why.openPart', { count: open, value: String(open) }),
      })
    }
    case 'other_series_suspected':
      return t('downloads.problems.why.other_series_suspected')
    case 'several_videos':
      return t('downloads.problems.why.several_videos', { count: countOf(problem.values?.count) })
    case 'multi_part':
      return t('downloads.problems.why.multi_part')
    case 'import_stalled':
      return t('downloads.problems.why.import_stalled')
    case 'too_many_files':
      return t('downloads.problems.why.too_many_files')
    case 'no_audio':
      return t('downloads.problems.why.no_audio')
    case 'album_single_file':
      return t('downloads.problems.why.album_single_file')
    case 'album_not_better':
      return t('downloads.problems.why.album_not_better')
    case 'album_tracks_missing':
      return t('downloads.problems.why.album_tracks_missing')
    default:
      return t('downloads.problems.why.unknown', { code: problem.code })
  }
}

/** Warum das Ablegen scheiterte, nach `values.reason`: ein Satz mit dem naechsten Schritt, sonst der allgemeine. */
function importFailedWhyText(t: TFunction, values: Record<string, unknown>): string {
  const episodes = typeof values.episodes === 'string' ? values.episodes : ''
  switch (values.reason) {
    case 'destination_exists':
      return t('downloads.problems.why.destinationExists')
    case 'foreign_file':
      return t('downloads.problems.why.foreignFile', { episodes })
    case 'no_folder':
      return t('downloads.problems.why.importReason.no_folder')
    case 'folder_not_writable':
      return t('downloads.problems.why.importReason.folder_not_writable')
    case 'folder_not_visible':
      return t('downloads.problems.why.importReason.folder_not_visible')
    case 'version_gone':
      return t('downloads.problems.why.importReason.version_gone')
    case 'version_fed_by_source':
      return t('downloads.problems.why.importReason.version_fed_by_source')
    case 'transfer_failed':
      return t('downloads.problems.why.importReason.transfer_failed')
    case 'database_busy':
      return t('downloads.problems.why.importReason.database_busy')
    default:
      return t('downloads.problems.why.import_failed')
  }
}

/** Der Umfang eines Serien-Downloads unter dem Titel: "S02E05", "S02E05, S02E06", "Staffel 2, 10 Folgen". null bei Filmen. */
export function scopeText(t: TFunction, scope: DownloadScope | null | undefined, language: string): string | null {
  if (!scope) return null
  const count = scope.episodes.length
  if (count === 0) return null
  if (count <= 2) return scope.episodes.join(', ')
  if (scope.season !== null && scope.kind === 'season') {
    return t('downloads.scope.season', { season: formatNumber(scope.season, language), count, value: formatNumber(count, language) })
  }
  return t('downloads.scope.episodes', { count, value: formatNumber(count, language) })
}

/**
 * Die Zahlen eines abgelegten Serien-Downloads im Verlauf: "8 Folgen abgelegt · ausgelassen, ...: S02E01 · nicht abgelegt:
 * S02E09 · fehlten im Paket: S02E07". Leeres faellt weg. null bei Filmen.
 */
export function scopeCountsText(t: TFunction, scope: DownloadScope | null | undefined, language: string): string | null {
  if (!scope) return null
  if (scope.kind === 'album') {
    // Seit M4: Dateien abgelegt, Titel der Ausgabe ohne Datei.
    const parts = [t('downloads.history.filesFiled', { count: scope.filed, value: formatNumber(scope.filed, language) })]
    if (scope.missing > 0) parts.push(t('downloads.history.tracksMissing', { count: scope.missing, value: formatNumber(scope.missing, language) }))
    return parts.join(' · ')
  }
  const parts = [t('downloads.history.filed', { count: scope.filed, value: formatNumber(scope.filed, language) })]
  if (scope.skipped_codes && scope.skipped_codes.length > 0) parts.push(t('downloads.history.skippedCodes', { codes: scope.skipped_codes.join(', ') }))
  if (scope.not_filed_codes && scope.not_filed_codes.length > 0) parts.push(t('downloads.history.notFiledCodes', { codes: scope.not_filed_codes.join(', ') }))
  if (scope.missing_codes && scope.missing_codes.length > 0) parts.push(t('downloads.history.missingCodes', { codes: scope.missing_codes.join(', ') }))
  return parts.join(' · ')
}

/** Restzeit in Sekunden als "12 Min.", "2 Std. 5 Min." oder "3 Tage". */
export function remainingText(t: TFunction, seconds: number, language: string): string {
  const whole = Math.max(0, Math.floor(seconds))
  if (whole < 60) return t('downloads.time.underMinute')
  if (whole < 3600) return t('downloads.time.minutes', { minutes: formatNumber(Math.floor(whole / 60), language) })
  if (whole < 86400) {
    return t('downloads.time.hours', {
      hours: formatNumber(Math.floor(whole / 3600), language),
      minutes: formatNumber(Math.floor((whole % 3600) / 60), language),
    })
  }
  const days = Math.floor(whole / 86400)
  return t('downloads.time.days', { count: days, value: formatNumber(days, language) })
}

/** Warum ein Download fehlgeschlagen ist, in einem Satz. null ohne Grund oder bei einem unbekannten. */
export function failedReasonText(t: TFunction, reason: string | null, client: string): string | null {
  switch (reason) {
    case 'client_failed':
      return t('downloads.history.failed.client_failed', { client })
    case 'encrypted':
      return t('downloads.history.failed.encrypted')
    case 'not_taken':
      return t('downloads.history.failed.not_taken', { client })
    default:
      return null
  }
}

/** Wie die Datei in den Ordner der Fassung kam, in schlichten Worten und ohne Fachwort. */
export function transferText(t: TFunction, transfer: DownloadTransfer | null): string | null {
  switch (transfer) {
    case 'hardlink':
      return t('downloads.history.transfer.hardlink')
    case 'copy':
      return t('downloads.history.transfer.copy')
    case 'move':
      return t('downloads.history.transfer.move')
    case 'unpacked':
      return t('downloads.history.transfer.unpacked')
    default:
      return null
  }
}

/** Der Filmordner aus `imported_path` (Ordner und Datei relativ zum Ordner der Fassung). null, wenn er fehlt. */
function movieFolderOf(path: string | null | undefined): string | null {
  if (typeof path !== 'string') return null
  const parts = path.split('/').filter((part) => part !== '')
  return parts.length >= 2 ? parts.slice(0, -1).join('/') : null
}

/**
 * Wo der Film jetzt liegt, zuerst im Verlauf: die Fassung und ihr Filmordner. Schickt der Server den Pfad nicht,
 * nur die Fassung. Befund 6 vom 14.09.2026: "Als Hardlink abgelegt" las sich, als bliebe der Film im Download-Ordner.
 */
export function placeText(t: TFunction, download: Pick<Download, 'version' | 'imported_path'>): string {
  const folder = movieFolderOf(download.imported_path)
  return folder !== null
    ? t('downloads.history.place.folder', { version: download.version.label, folder })
    : t('downloads.history.place.version', { version: download.version.label })
}

/** Was SABnzbd zu einem Fehlschlag sagte, in der Sprache der Oberflaeche. null ohne Code. */
export function failedDetailText(t: TFunction, detail: string | null | undefined, client: string): string | null {
  switch (detail) {
    case 'repair_failed':
      return t('downloads.history.detail.repair_failed', { client })
    case 'incomplete':
      return t('downloads.history.detail.incomplete', { client })
    case 'not_on_server':
      return t('downloads.history.detail.not_on_server', { client })
    case 'password':
      return t('downloads.history.detail.password', { client })
    case 'unpack_failed':
      return t('downloads.history.detail.unpack_failed', { client })
    case 'encrypted':
      return t('downloads.history.detail.encrypted', { client })
    case 'unwanted_extension':
      return t('downloads.history.detail.unwanted_extension', { client })
    case 'duplicate':
      return t('downloads.history.detail.duplicate', { client })
    case 'aborted':
      return t('downloads.history.detail.aborted', { client })
    case 'other':
      return t('downloads.history.detail.other', { client })
    default:
      return null
  }
}

/** Was aus einem Fehlschlag wurde (22.09.2026), in einem Satz. null, wenn der Server nichts dazu sagt. */
export function aftermathText(t: TFunction, aftermath: DownloadAftermath | null | undefined, language: string): string | null {
  if (!aftermath) return null
  const at = typeof aftermath.at === 'string' && aftermath.at !== '' ? formatDateTime(aftermath.at, language) : null
  switch (aftermath.kind) {
    case 'replaced':
      return t('downloads.history.aftermath.replaced', { release: aftermath.release ?? '' })
    case 'kept_file':
      return t('downloads.history.aftermath.kept_file')
    case 'waiting_limit':
      return at !== null ? t('downloads.history.aftermath.waiting_limit', { at }) : t('downloads.history.aftermath.waitingLimitSoon')
    case 'nothing_found':
      return at !== null ? t('downloads.history.aftermath.nothing_found', { at }) : t('downloads.history.aftermath.nothingFoundLater')
    case 'searching':
      return t('downloads.history.aftermath.searching')
    case 'schedule':
      return t('downloads.history.aftermath.schedule')
    case 'owner':
      return t('downloads.history.aftermath.owner')
    default:
      return null
  }
}
