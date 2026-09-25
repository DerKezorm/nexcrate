import { useTranslation } from 'react-i18next'

import type { HistoryEntry } from '../../api/types'
import { Symbol, type SymbolName } from '../../components/Symbol'
import { formatDateTime, formatNumber } from '../../lib/format'
import { STATE_LOOK } from '../../lib/states'
import { AlbumFiledMapping } from './AlbumFiledMapping'
import { failureReasonText } from './blocklistText'

/** Wie ein Ereignis aussieht. Seit Schritt 3 kommen "zum Laden übergeben" und "fehlgeschlagen" dazu, mit der Uebernahme "übernommen". */
/** /api/v1 V2: `requested` und `withdrawn` tragen den Namen des Schluessels, nach einem Zeilenumbruch die Kennung des Programms. */
function programDetail(detail: string | null): { name: string; origin: string | null } | null {
  if (detail === null || detail.trim() === '') return null
  const [name, ...rest] = detail.split('\n')
  const origin = rest.join('\n').trim()
  return { name: name.trim(), origin: origin === '' ? null : origin }
}

/** V2: `files_deleted` und `file_restored` tragen die Zahl, nach einem Leerzeichen den Namen des Schluessels, wenn ein Programm es war. */
function binDetail(detail: string | null): { count: number; name: string | null } | null {
  const match = /^(\d+)(?: (.+))?$/s.exec(detail ?? '')
  if (match === null) return null
  return { count: Number(match[1]), name: match[2]?.trim() || null }
}

/** V3: die Aktionen, die ein Programm an einem Download ausfuehren kann (`operated`). */
const OPERATED_ACTIONS = new Set(['retry', 'remove', 'remove_and_search', 'clear', 'search', 'confirm_mapping', 'finish', 'assign'])

/** V3: `operated` traegt die Aktion, nach einem Leerzeichen den Namen des Schluessels. */
function operatedDetail(detail: string | null): { action: string; name: string } | null {
  const match = /^([a-z_]+) (.+)$/s.exec(detail ?? '')
  return match === null ? null : { action: match[1], name: match[2].trim() }
}

function lookOf(event: string): { chip: string; symbol: SymbolName } {
  switch (event) {
    case 'operated':
      return { chip: 'border-ink-600 bg-ink-900 text-mist-400', symbol: 'swap' }
    case 'requested':
      return { chip: STATE_LOOK.downloading.chip, symbol: 'link' }
    case 'withdrawn':
      return { chip: 'border-ink-600 bg-ink-900 text-mist-400', symbol: 'back' }
    case 'files_deleted':
      return { chip: 'border-ink-600 bg-ink-900 text-mist-400', symbol: 'trash' }
    case 'file_restored':
      return { chip: STATE_LOOK.available.chip, symbol: 'folder' }
    case 'imported':
      return { chip: STATE_LOOK.available.chip, symbol: 'check' }
    case 'taken_over':
      return { chip: STATE_LOOK.available.chip, symbol: 'import' }
    case 'found_on_disk':
    case 'restored':
      return { chip: STATE_LOOK.available.chip, symbol: 'folder' }
    case 'takeover_undone':
      return { chip: 'border-ink-600 bg-ink-900 text-mist-400', symbol: 'back' }
    case 'grabbed':
      return { chip: STATE_LOOK.downloading.chip, symbol: 'download' }
    case 'failed':
      return { chip: STATE_LOOK.problem.chip, symbol: 'alert' }
    // Serien (S1): TMDB hat umnummeriert oder Folgen nachtraeglich eingetragen.
    case 'renumbered':
      return { chip: 'border-ink-600 bg-ink-900 text-mist-400', symbol: 'swap' }
    case 'episodes_late':
      return { chip: 'border-ink-600 bg-ink-900 text-mist-400', symbol: 'calendar' }
    // Serien (S4): Folgen eines Downloads abgelegt, eine Datei nach dem neuen Titel umbenannt.
    case 'episodes_filed':
      return { chip: STATE_LOOK.available.chip, symbol: 'check' }
    // Seit S6 auch: Die Reparatur einer falschen Bruecke zu TVDB hat eine Datei umgehaengt.
    case 'episode_renamed':
    case 'file_relinked':
      return { chip: 'border-ink-600 bg-ink-900 text-mist-400', symbol: 'swap' }
    // Musik M1: neue Release-Group, geaenderte Einstufung, bei MusicBrainz verschwunden, andere Zielausgabe.
    case 'album_appeared':
      return { chip: 'border-ink-600 bg-ink-900 text-mist-400', symbol: 'plus' }
    case 'album_type_changed':
      return { chip: 'border-ink-600 bg-ink-900 text-mist-400', symbol: 'swap' }
    case 'album_gone':
      return { chip: STATE_LOOK.unmonitored.chip, symbol: 'eyeOff' }
    case 'target_changed':
      return { chip: 'border-ink-600 bg-ink-900 text-mist-400', symbol: 'layers' }
    // Musik M4: ein Albendownload hat Dateien abgelegt, die Tags wurden neu geschrieben.
    case 'album_filed':
      return { chip: STATE_LOOK.available.chip, symbol: 'check' }
    case 'tags_written':
      return { chip: 'border-ink-600 bg-ink-900 text-mist-400', symbol: 'note' }
    default:
      return { chip: 'border-ink-600 bg-ink-900 text-mist-400', symbol: 'plus' }
  }
}

/** Ein Detail der Form "A > B", wie es Musik M1 fuer Typ- und Zielwechsel schreibt. null, wenn es nicht so aussieht. */
function arrowDetail(detail: string | null): { from: string; to: string } | null {
  if (detail === null) return null
  const at = detail.indexOf(' > ')
  if (at < 0) return null
  return { from: detail.slice(0, at), to: detail.slice(at + 3) }
}

/** Das Detail von `imported`: die Qualitaet und seit 3c, wie viele Untertitel dazukamen, etwa "Bluray-1080p, 5 subtitles". */
function importedDetail(detail: string): { quality: string | null; subtitles: number | null } {
  const found = /^(?:(.*), )?(\d+) subtitles?$/.exec(detail)
  if (!found) return { quality: detail, subtitles: null }
  return { quality: found[1] ?? null, subtitles: Number(found[2]) }
}

/** Das Detail von `renumbered`, etwa "S01E05 S01E06": alte und neue Nummer. null, wenn es nicht so aussieht. */
function renumberedDetail(detail: string | null): { from: string; to: string } | null {
  const parts = (detail ?? '').trim().split(/\s+/)
  return parts.length === 2 && parts[0] !== '' ? { from: parts[0], to: parts[1] } : null
}

/** Das Detail von `episodes_late`: wie viele Folgen. null ohne lesbare Zahl. */
function lateDetail(detail: string | null): number | null {
  const value = Number((detail ?? '').trim())
  return detail !== null && detail.trim() !== '' && Number.isInteger(value) && value > 0 ? value : null
}

/** Das Detail von `episodes_filed`, etwa "filed=7 skipped=1 missing=S02E07,S02E08 not_filed=S02E09". */
function filedDetail(detail: string | null): { filed: number; skipped: number; missing: string[]; notFiled: string[] } | null {
  const values: Record<string, string> = {}
  for (const part of (detail ?? '').trim().split(/\s+/)) {
    const at = part.indexOf('=')
    if (at > 0) values[part.slice(0, at)] = part.slice(at + 1)
  }
  if (values.filed === undefined || !/^\d+$/.test(values.filed)) return null
  const codes = (value: string | undefined) => (value ? value.split(',').filter((code) => /^S\d+E\d+$/.test(code)) : [])
  return { filed: Number(values.filed), skipped: /^\d+$/.test(values.skipped ?? '') ? Number(values.skipped) : 0, missing: codes(values.missing), notFiled: codes(values.not_filed) }
}

/** Das Detail von `album_filed`, etwa "filed=14 missing=0 release=7 download=4": wie viele Dateien abgelegt wurden. */
function albumFiledDetail(detail: string | null): { filed: number } | null {
  const found = /(?:^|\s)filed=(\d+)(?:\s|$)/.exec(detail ?? '')
  return found ? { filed: Number(found[1]) } : null
}

/** Ereignisse, deren Detail im Satz steht und nicht noch einmal darunter. */
function detailInSentence(entry: HistoryEntry): boolean {
  switch (entry.event) {
    case 'taken_over':
    case 'takeover_undone':
    case 'requested':
    case 'withdrawn':
      return true
    case 'files_deleted':
    case 'file_restored':
      return binDetail(entry.detail) !== null
    case 'operated': {
      const operated = operatedDetail(entry.detail)
      return operated !== null && OPERATED_ACTIONS.has(operated.action)
    }
    case 'renumbered':
      return renumberedDetail(entry.detail) !== null
    case 'episodes_late':
      return lateDetail(entry.detail) !== null
    case 'episodes_filed':
      return filedDetail(entry.detail) !== null
    case 'episode_renamed':
      return true
    case 'file_relinked':
      return renumberedDetail(entry.detail) !== null
    case 'album_type_changed':
      return arrowDetail(entry.detail) !== null
    case 'target_changed':
      return arrowDetail(entry.detail) !== null
    case 'album_filed':
      return albumFiledDetail(entry.detail) !== null
    default:
      return false
  }
}

/** Was mit dem Titel passiert ist, in der Reihenfolge des Servers. */
export function HistoryList({ entries }: { entries: HistoryEntry[] }) {
  const { t, i18n } = useTranslation()

  if (entries.length === 0) return <p className="text-sm text-mist-500">{t('title.history.empty')}</p>

  function text(entry: HistoryEntry): string {
    switch (entry.event) {
      case 'added':
        return t('title.history.added', { version: entry.version })
      case 'imported':
        return t('title.history.imported', { version: entry.version })
      case 'relocated':
        return t('title.history.relocated', { version: entry.version, folder: entry.detail ?? '' })
      case 'grabbed':
        return t('title.history.grabbed', { version: entry.version })
      case 'failed':
        return t('title.history.failed', { version: entry.version })
      case 'taken_over':
        // Das Detail ist der Name der Verbindung. Er steht im Satz, nicht noch einmal darunter.
        return entry.detail ? t('title.history.takenOver', { version: entry.version, name: entry.detail }) : t('title.history.takenOverUnnamed', { version: entry.version })
      case 'takeover_undone':
        // Auch hier ist das Detail der Name der Verbindung und steht im Satz.
        return entry.detail
          ? t('title.history.takeoverUndone', { version: entry.version, name: entry.detail })
          : t('title.history.takeoverUndoneUnnamed', { version: entry.version })
      // Library from disk: assigned by hand (detail the quality) or restored from its release.nex (detail the file name).
      case 'found_on_disk':
        return t('title.history.foundOnDisk', { version: entry.version })
      case 'restored':
        return t('title.history.restored', { version: entry.version })
      // Serien (S1): Das Detail sind alte und neue Nummer, oder wie viele Folgen nachtraeglich kamen.
      case 'renumbered': {
        const renumbered = renumberedDetail(entry.detail)
        if (renumbered !== null) return t('series.history.renumbered', renumbered)
        break
      }
      case 'episodes_late': {
        const count = lateDetail(entry.detail)
        if (count !== null) return t('series.history.episodesLate', { count })
        break
      }
      case 'episodes_filed': {
        const filed = filedDetail(entry.detail)
        if (filed !== null) return t('series.history.episodesFiled', { version: entry.version, count: filed.filed })
        break
      }
      case 'episode_renamed':
        return t('series.history.episodeRenamed', { version: entry.version, codes: (entry.detail ?? '').split(',').join(', ') })
      // Das Detail sind die Folgen vorher und nachher, "-" fuer keine.
      case 'file_relinked': {
        const moved = renumberedDetail(entry.detail)
        if (moved === null) break
        const codes = (value: string) => value.split(',').join(', ')
        if (moved.from === '-') return t('series.history.fileLinked', { version: entry.version, to: codes(moved.to) })
        if (moved.to === '-') return t('series.history.fileReleased', { version: entry.version, from: codes(moved.from) })
        return t('series.history.fileRelinked', { version: entry.version, from: codes(moved.from), to: codes(moved.to) })
      }
      // Musik M1: eine neue Release-Group, eine geaenderte Einstufung, bei MusicBrainz verschwunden, eine andere Zielausgabe.
      case 'album_appeared':
        return t('title.history.albumAppeared')
      case 'album_type_changed': {
        const changed = arrowDetail(entry.detail)
        if (changed !== null) return t('title.history.albumTypeChanged', changed)
        break
      }
      case 'album_gone':
        return t('title.history.albumGone')
      case 'target_changed': {
        const changed = arrowDetail(entry.detail)
        if (changed !== null) return t('title.history.targetChanged', changed)
        break
      }
      case 'album_filed': {
        const filed = albumFiledDetail(entry.detail)
        if (filed !== null) return t('title.history.albumFiled', { version: entry.version, count: filed.filed })
        break
      }
      case 'tags_written':
        return t('title.history.tagsWritten', { version: entry.version })
      // /api/v1 V2: ein Programm hat angefragt oder zurueckgenommen; der Name ist der seines Schluessels.
      case 'requested':
      case 'withdrawn': {
        const program = programDetail(entry.detail)
        if (program !== null) return t(entry.event === 'requested' ? 'title.history.requested' : 'title.history.withdrawn', { version: entry.version, name: program.name })
        break
      }
      case 'files_deleted': {
        const bin = binDetail(entry.detail)
        if (bin === null) break
        const value = formatNumber(bin.count, i18n.language)
        return bin.name
          ? t('title.history.filesDeletedBy', { version: entry.version, count: bin.count, value, name: bin.name })
          : t('title.history.filesDeleted', { version: entry.version, count: bin.count, value })
      }
      case 'file_restored': {
        const bin = binDetail(entry.detail)
        if (bin === null) break
        return bin.name ? t('title.history.fileRestoredBy', { version: entry.version, name: bin.name }) : t('title.history.fileRestored', { version: entry.version })
      }
      // /api/v1 V3: ein Programm hat einen Download bedient.
      case 'operated': {
        const operated = operatedDetail(entry.detail)
        if (operated === null) break
        const action = operatedAction(operated.action)
        if (action === null) break
        return t('title.history.operated', { version: entry.version, action, name: operated.name })
      }
    }
    // Ein Ereignis, das diese Oberflaeche noch nicht kennt (oder ein unlesbares Detail), steht da, wie es kommt.
    // (Die Aktionen von `operated` stehen in operatedAction.)
    return t('title.history.other', { version: entry.version, event: String(entry.event) })
  }

  function operatedAction(action: string): string | null {
    switch (action) {
      case 'retry':
        return t('title.history.operatedAction.retry')
      case 'remove':
        return t('title.history.operatedAction.remove')
      case 'remove_and_search':
        return t('title.history.operatedAction.remove_and_search')
      case 'clear':
        return t('title.history.operatedAction.clear')
      case 'search':
        return t('title.history.operatedAction.search')
      case 'confirm_mapping':
        return t('title.history.operatedAction.confirm_mapping')
      case 'finish':
        return t('title.history.operatedAction.finish')
      case 'assign':
        return t('title.history.operatedAction.assign')
      default:
        return null
    }
  }

  return (
    <ol className="flex flex-col">
      {entries.map((entry, index) => {
        const look = lookOf(entry.event)
        const imported = entry.event === 'imported' && entry.detail ? importedDetail(entry.detail) : null
        return (
          <li key={`${entry.at}-${entry.event}-${entry.version}-${index}`} className="flex gap-3 border-t border-ink-700/60 py-3 first:border-t-0 first:pt-0">
            <span className={'mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full border ' + look.chip}>
              <Symbol name={look.symbol} className="h-4 w-4" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm text-mist-200">{text(entry)}</p>
              {/* Bei `failed` ist das Detail ein Code wie `dangerous_file`, bei den anderen etwa die Qualitaet oder das Release. */}
              {entry.detail !== null && entry.detail !== '' && entry.event === 'failed' && <p className="text-sm text-mist-400">{failureReasonText(t, entry.detail)}</p>}
              {/* Beim Import die Qualitaet wie sie kommt, die Zahl der Untertitel in der Sprache der Oberflaeche. */}
              {imported !== null && imported.quality && <p className="font-mono text-xs wrap-anywhere text-mist-600">{imported.quality}</p>}
              {entry.event === 'episodes_filed' && <FiledLines detail={entry.detail} />}
              {entry.event === 'requested' && programDetail(entry.detail)?.origin && (
                <p className="font-mono text-xs wrap-anywhere text-mist-600">{programDetail(entry.detail)?.origin}</p>
              )}
              {entry.event === 'album_filed' && typeof entry.download_id === 'number' && <AlbumFiledMapping downloadId={entry.download_id} />}
              {imported !== null && imported.subtitles !== null && (
                <p className="text-xs text-mist-500">{t('title.history.subtitles', { count: imported.subtitles })}</p>
              )}
              {imported === null && entry.detail !== null && entry.detail !== '' && entry.event !== 'failed' && !detailInSentence(entry) && (
                <p className="font-mono text-xs wrap-anywhere text-mist-600">{entry.detail}</p>
              )}
            </div>
            <time dateTime={entry.at} className="shrink-0 text-xs text-mist-500 tabular-nums">
              {formatDateTime(entry.at, i18n.language)}
            </time>
          </li>
        )
      })}
    </ol>
  )
}

/** Unter "Folgen abgelegt": was ausgelassen wurde, was im Paket fehlte und was bewusst nicht abgelegt wurde. */
function FiledLines({ detail }: { detail: string | null }) {
  const { t } = useTranslation()
  const filed = filedDetail(detail)
  if (filed === null) return null
  return (
    <>
      {filed.skipped > 0 && <p className="text-xs text-mist-500">{t('series.history.skipped', { count: filed.skipped })}</p>}
      {filed.missing.length > 0 && <p className="text-xs text-mist-500">{t('series.history.missing', { codes: filed.missing.join(', '), count: filed.missing.length })}</p>}
      {filed.notFiled.length > 0 && <p className="text-xs text-mist-500">{t('series.history.notFiled', { codes: filed.notFiled.join(', '), count: filed.notFiled.length })}</p>}
    </>
  )
}
