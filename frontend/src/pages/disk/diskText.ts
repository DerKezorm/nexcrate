/**
 * The words and rules of the folder page, without React: which states a tab lists, the sentence of every state, how a
 * proposal was found, and the phases and outcomes of a job.
 */

import type { TFunction } from 'i18next'

import { API_BASE } from '../../api/client'
import type { DiskCounts, DiskFolder, DiskFolderState, DiskJob, DiskRoot, DiskRootKind, Proposal } from '../../api/types'

/** Ob die Seite Filmordner, Serienordner (S6, Entscheidung 25) oder Albumordner (Musik M6) zeigt. Steht als `art` in der Adresse. */
export const DISK_KINDS: readonly DiskRootKind[] = ['movie', 'series', 'album']

/** ⚠️ Deutsche Woerter in der Adresse, englische Werte im Code, wie unter Einstellungen. */
const KIND_TO_ADDRESS: Record<DiskRootKind, string> = { movie: 'filme', series: 'serien', album: 'musik' }

/** Ein unbekannter oder fehlender Wert heisst Filme. */
export function diskKindFromAddress(value: string | null): DiskRootKind {
  return DISK_KINDS.find((kind) => KIND_TO_ADDRESS[kind] === value) ?? 'movie'
}

export function addressOfDiskKind(kind: DiskRootKind): string {
  return KIND_TO_ADDRESS[kind]
}

/** Die Art einer Wurzel. Ein Server von vor S6 schickt keine; dann sind es Filmordner. */
export function rootKind(root: DiskRoot): DiskRootKind {
  return root.kind === 'series' || root.kind === 'album' ? root.kind : 'movie'
}

export function rootsOfKind(roots: readonly DiskRoot[], kind: DiskRootKind): DiskRoot[] {
  return roots.filter((root) => rootKind(root) === kind)
}

/** The tabs of the page, in their order. */
export type DiskTab = 'restorable' | 'proposal' | 'unknown' | 'conflict' | 'radarr' | 'sonarr' | 'lidarr' | 'library' | 'ignored'

export const DISK_TABS: readonly DiskTab[] = ['restorable', 'proposal', 'unknown', 'conflict', 'radarr', 'library', 'ignored']

/** Serienordner: dieselben Reiter, nur "In Sonarr" statt "In Radarr" (S6, P2). */
export const SERIES_TABS: readonly DiskTab[] = ['restorable', 'proposal', 'unknown', 'conflict', 'sonarr', 'library', 'ignored']

/** Albumordner (Musik M6): "In Lidarr" statt "In Radarr". */
export const ALBUM_TABS: readonly DiskTab[] = ['restorable', 'proposal', 'unknown', 'conflict', 'lidarr', 'library', 'ignored']

export function tabsOf(kind: DiskRootKind): readonly DiskTab[] {
  return kind === 'series' ? SERIES_TABS : kind === 'album' ? ALBUM_TABS : DISK_TABS
}

/**
 * The states a tab lists, each as a group of its own in this order. "Wiederherstellen" shows moved movies first: they
 * have a `release.nex` too. "Unbekannt" lists what cannot be assigned in this block behind the unknown folders, so
 * nothing the scan saw is hidden.
 */
export const TAB_STATES: Record<DiskTab, readonly DiskFolderState[]> = {
  restorable: ['moved', 'restorable'],
  proposal: ['proposal'],
  unknown: ['unknown', 'file', 'disc', 'no_video', 'unreadable'],
  conflict: ['conflict'],
  radarr: ['radarr'],
  sonarr: ['sonarr'],
  lidarr: ['lidarr'],
  library: ['library'],
  ignored: ['ignored'],
}

/** Serienordner kennen weder verschobene Ordner noch lose Dateien oder Discs (`series_scan`). */
export const SERIES_TAB_STATES: Record<DiskTab, readonly DiskFolderState[]> = {
  restorable: ['restorable'],
  proposal: ['proposal'],
  unknown: ['unknown', 'no_video'],
  conflict: ['conflict'],
  radarr: ['radarr'],
  sonarr: ['sonarr'],
  lidarr: ['lidarr'],
  library: ['library'],
  ignored: ['ignored'],
}

/** Albumordner kennen nur Ordner mit Audiodateien (`album_scan`). */
export const ALBUM_TAB_STATES: Record<DiskTab, readonly DiskFolderState[]> = {
  ...SERIES_TAB_STATES,
  unknown: ['unknown'],
}

export function statesOf(kind: DiskRootKind, tab: DiskTab): readonly DiskFolderState[] {
  return kind === 'series' ? SERIES_TAB_STATES[tab] : kind === 'album' ? ALBUM_TAB_STATES[tab] : TAB_STATES[tab]
}

/** The count of one state over every root, or over one root. Missing counts are 0. */
export function countOf(roots: readonly DiskRoot[], state: DiskFolderState, rootId: number | null = null): number {
  return roots.filter((root) => rootId === null || root.id === rootId).reduce((sum, root) => sum + (root.counts?.[state] ?? 0), 0)
}

export function tabCount(roots: readonly DiskRoot[], tab: DiskTab, rootId: number | null = null, kind: DiskRootKind = 'movie'): number {
  return statesOf(kind, tab).reduce((sum, state) => sum + countOf(roots, state, rootId), 0)
}

/** The tab the page opens on: the first of the first three that is not empty, else "Wiederherstellen". */
export function initialTab(roots: readonly DiskRoot[], kind: DiskRootKind = 'movie'): DiskTab {
  for (const tab of ['restorable', 'proposal', 'unknown'] as const) {
    if (tabCount(roots, tab, null, kind) > 0) return tab
  }
  return 'restorable'
}

/** The counts a root shows under its path: only states that occurred, in the order of the tabs. */
export function shownCounts(counts: DiskCounts | null): [DiskFolderState, number][] {
  if (!counts) return []
  const order: DiskFolderState[] = ['library', 'restorable', 'moved', 'proposal', 'unknown', 'conflict', 'radarr', 'sonarr', 'lidarr', 'file', 'disc', 'no_video', 'unreadable', 'ignored']
  return order.flatMap((state) => {
    const value = counts[state]
    return typeof value === 'number' && value > 0 ? [[state, value] as [DiskFolderState, number]] : []
  })
}

export function stateLabel(t: TFunction, state: DiskFolderState): string {
  switch (state) {
    case 'library':
      return t('disk.tabs.library')
    case 'restorable':
      return t('disk.tabs.restorable')
    case 'moved':
      return t('disk.list.group.moved')
    case 'proposal':
      return t('disk.tabs.proposal')
    case 'unknown':
      return t('disk.tabs.unknown')
    case 'conflict':
      return t('disk.tabs.conflict')
    case 'radarr':
      return t('disk.tabs.radarr')
    case 'sonarr':
      return t('disk.series.tabs.sonarr')
    case 'lidarr':
      return t('disk.album.tabs.lidarr')
    case 'file':
      return t('disk.list.group.file')
    case 'disc':
      return t('disk.list.group.disc')
    case 'no_video':
      return t('disk.list.group.no_video')
    case 'unreadable':
      return t('disk.list.group.unreadable')
    case 'ignored':
      return t('disk.tabs.ignored')
  }
}

/** The sentence of a row's state. A `radarr` row names its connection when the server sent one. */
export function stateText(t: TFunction, folder: DiskFolder, kind: DiskRootKind = 'movie'): string {
  if (kind === 'series') return seriesStateText(t, folder)
  if (kind === 'album') return albumStateText(t, folder)
  switch (folder.state) {
    case 'restorable':
      return t('disk.state.restorable')
    case 'proposal':
      return t('disk.state.proposal')
    case 'unknown':
      return t('disk.state.unknown')
    case 'conflict':
      return t('disk.state.conflict')
    case 'radarr':
      return folder.source_name ? t('disk.state.radarr', { name: folder.source_name }) : t('disk.state.radarrUnnamed')
    case 'moved':
      return t('disk.state.moved')
    case 'file':
      return t('disk.state.file')
    case 'disc':
      return t('disk.state.disc')
    case 'no_video':
      return t('disk.state.no_video')
    case 'unreadable':
      return t('disk.state.unreadable')
    case 'library':
      return t('disk.state.library')
    case 'ignored':
      return t('disk.state.ignored')
    default:
      return t('disk.state.unknown')
  }
}

/**
 * Der Satz eines Serienordners (S6). Eine `sonarr`-Zeile nennt ihre Verbindung, wenn der Server sie mitschickt;
 * angeboten wird an ihr nichts, sie gehoert nach der Uebernahme hierher.
 */
function seriesStateText(t: TFunction, folder: DiskFolder): string {
  switch (folder.state) {
    case 'library':
      return t('disk.series.state.library')
    case 'sonarr':
      return folder.source_name ? t('disk.series.state.sonarr', { name: folder.source_name }) : t('disk.series.state.sonarrUnnamed')
    case 'restorable':
      return t('disk.series.state.restorable')
    case 'proposal':
      return t('disk.series.state.proposal')
    case 'conflict':
      return t('disk.series.state.conflict')
    case 'no_video':
      return t('disk.series.state.no_video')
    case 'ignored':
      return t('disk.series.state.ignored')
    default:
      return t('disk.series.state.unknown')
  }
}

/** Der Satz eines Albumordners (Musik M6). Eine `lidarr`-Zeile gehoert nach der Uebernahme hierher; angeboten wird nichts. */
function albumStateText(t: TFunction, folder: DiskFolder): string {
  switch (folder.state) {
    case 'library':
      return t('disk.album.state.library')
    case 'lidarr':
      return folder.source_name ? t('disk.album.state.lidarr', { name: folder.source_name }) : t('disk.album.state.lidarrUnnamed')
    case 'restorable':
      return t('disk.album.state.restorable')
    case 'proposal':
      return t('disk.album.state.proposal')
    case 'conflict':
      return t('disk.album.state.conflict')
    case 'ignored':
      return t('disk.album.state.ignored')
    default:
      return t('disk.album.state.unknown')
  }
}

/** Woher ein Vorschlag fuer einen Albumordner kommt. */
export function albumFromText(t: TFunction, from: string): string {
  switch (from) {
    case 'companion':
      return t('disk.album.from.companion')
    case 'tags':
      return t('disk.album.from.tags')
    default:
      return t('disk.album.from.library')
  }
}

/** Die Abzeichenfarbe eines Zustands: Konflikte rosa, Bekanntes gruen, Angebote bernstein, der Rest grau. */
export function stateTone(state: DiskFolderState): 'neutral' | 'ok' | 'bad' | 'accent' {
  switch (state) {
    case 'library':
      return 'ok'
    case 'conflict':
      return 'bad'
    case 'restorable':
    case 'proposal':
      return 'accent'
    default:
      return 'neutral'
  }
}

/** Die Staffelordner einer Serienzeile, hoechstens ein paar; der Rest wird gezaehlt. */
export const SHOWN_SEASONS = 4

export function shownSeasons(folder: DiskFolder): { names: string[]; more: number } {
  const seasons = Array.isArray(folder.series?.seasons) ? folder.series.seasons.filter((name) => typeof name === 'string' && name !== '') : []
  return { names: seasons.slice(0, SHOWN_SEASONS), more: Math.max(0, seasons.length - SHOWN_SEASONS) }
}

/** How a proposal was found, in words. An unknown value reads as "gefunden". */
export function fromText(t: TFunction, from: string): string {
  switch (from) {
    case 'companion':
      return t('disk.row.from.companion')
    case 'name_number':
      return t('disk.row.from.name_number')
    case 'nfo_number':
      return t('disk.row.from.nfo_number')
    case 'title_year':
      return t('disk.row.from.title_year')
    case 'library':
      return t('disk.row.from.library')
    default:
      return t('disk.row.from.other')
  }
}

/** The poster of a proposal, served by nexcrate itself, or null without one. */
export function proposalPoster(proposal: Proposal): string | null {
  const file = proposal.poster_file
  if (typeof file !== 'string' || file === '' || file.includes('/') || file.includes('\\')) return null
  return `${API_BASE}/tmdb/poster/w185/${encodeURIComponent(file)}`
}

export function phaseText(t: TFunction, phase: string | null): string {
  switch (phase) {
    case 'listing':
      return t('disk.job.phase.listing')
    case 'folders':
      return t('disk.job.phase.folders')
    case 'radarr':
      return t('disk.job.phase.radarr')
    case 'matching':
      return t('disk.job.phase.matching')
    case 'tmdb':
      return t('disk.job.phase.tmdb')
    default:
      return t('disk.job.phase.other')
  }
}

export function jobTitle(t: TFunction, job: DiskJob, kind: DiskRootKind = 'movie'): string {
  const series = kind === 'series'
  switch (job.kind) {
    case 'restore':
      return kind === 'album' ? t('disk.album.job.restore') : series ? t('disk.series.job.restore') : t('disk.job.restore')
    case 'assign':
      return kind === 'album' ? t('disk.album.job.assign') : series ? t('disk.series.job.assign') : t('disk.job.assign')
    default:
      return t('disk.job.scan')
  }
}

export function jobDoneText(t: TFunction, job: DiskJob, kind: DiskRootKind = 'movie'): string {
  const series = kind === 'series'
  switch (job.kind) {
    case 'restore':
      return kind === 'album' ? t('disk.album.job.doneRestore') : series ? t('disk.series.job.doneRestore') : t('disk.job.done.restore')
    case 'assign':
      return kind === 'album' ? t('disk.album.job.doneAssign') : series ? t('disk.series.job.doneAssign') : t('disk.job.done.assign')
    default:
      return t('disk.job.done.scan')
  }
}

export function jobFailedText(t: TFunction, job: DiskJob, kind: DiskRootKind = 'movie'): string {
  const series = kind === 'series'
  switch (job.kind) {
    case 'restore':
      return kind === 'album' ? t('disk.album.job.failedRestore') : series ? t('disk.series.job.failedRestore') : t('disk.job.failed.restore')
    case 'assign':
      return kind === 'album' ? t('disk.album.job.failedAssign') : series ? t('disk.series.job.failedAssign') : t('disk.job.failed.assign')
    default:
      return t('disk.job.failed.scan')
  }
}

/**
 * The counts of a finished job as label and number. The server may nest them under `counts` or put them at the top
 * level; `conflicts` is a list of ids and counts by its length. Known outcomes get their word, a state its tab's name,
 * anything else stays as it came.
 */
export function jobOutcomes(t: TFunction, job: DiskJob): { key: string; label: string; value: number }[] {
  const result = job.result
  if (!result || typeof result !== 'object') return []
  const source: Record<string, unknown> = result.counts && typeof result.counts === 'object' ? result.counts : result
  const rows: { key: string; label: string; value: number }[] = []
  for (const [key, raw] of Object.entries(source)) {
    if (key === 'counts') continue
    const value = Array.isArray(raw) ? raw.length : typeof raw === 'number' ? raw : null
    if (value === null || value <= 0) continue
    rows.push({ key, label: outcomeLabel(t, key), value })
  }
  if (source !== result && Array.isArray(result.conflicts) && result.conflicts.length > 0 && !rows.some((row) => row.key === 'conflicts')) {
    rows.push({ key: 'conflicts', label: t('disk.job.outcome.conflict'), value: result.conflicts.length })
  }
  return rows
}

const STATES: readonly DiskFolderState[] = ['library', 'moved', 'radarr', 'sonarr', 'lidarr', 'restorable', 'proposal', 'unknown', 'conflict', 'file', 'disc', 'no_video', 'unreadable', 'ignored']

function outcomeLabel(t: TFunction, key: string): string {
  switch (key) {
    case 'restored':
      return t('disk.job.outcome.restored')
    case 'assigned':
      return t('disk.job.outcome.assigned')
    case 'skipped':
      return t('disk.job.outcome.skipped')
    case 'conflict':
    case 'conflicts':
      return t('disk.job.outcome.conflict')
    case 'failed':
      return t('disk.job.outcome.failed')
    case 'no_definition':
      return t('disk.series.job.noDefinition')
    case 'album_not_in_library':
      return t('disk.album.job.notInLibrary')
    case 'folders':
      return t('disk.job.outcome.folders')
    case 'roots':
      return t('disk.job.outcome.roots')
    default:
      return (STATES as readonly string[]).includes(key) ? stateLabel(t, key as DiskFolderState) : key
  }
}

/** The labels of the versions a root's movies go into, over the given roots, without repeats. */
export function versionLabelsOf(roots: readonly DiskRoot[]): string[] {
  const labels: string[] = []
  for (const root of roots) {
    for (const version of root.versions) {
      if (!labels.includes(version.label)) labels.push(version.label)
    }
  }
  return labels
}

/** The version labels named in the `release.nex` files of the given rows, without repeats. */
export function companionLabelsOf(folders: readonly DiskFolder[]): string[] {
  const labels: string[] = []
  for (const folder of folders) {
    for (const entry of folder.companion?.entries ?? []) {
      if (typeof entry?.version === 'string' && entry.version !== '' && !labels.includes(entry.version)) labels.push(entry.version)
    }
  }
  return labels
}

/** The largest video of a row, or null. The server sends them largest first; this does not rely on it. */
export function largestVideo(folder: DiskFolder): DiskFolder['videos'][number] | null {
  const videos = Array.isArray(folder.videos) ? folder.videos.filter((video) => video && typeof video.name === 'string') : []
  if (videos.length === 0) return null
  return videos.reduce((best, video) => (video.size_bytes > best.size_bytes ? video : best), videos[0])
}
