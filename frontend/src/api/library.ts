import { api } from './client'
import type {
  DeletedFiles,
  DeleteFilesRequest,
  EpisodeChoice,
  LibraryAdd,
  LibraryPage,
  LibrarySort,
  LibraryStats,
  MediaKind,
  RemovedMany,
  SeasonEpisodes,
  SeriesAdd,
  SeriesPreview,
  SeriesType,
  SeriesTypeProposals,
  SeriesTypeRun,
  SwitchRequest,
  TitleDetail,
  TitleVersionChange,
  VersionState,
  WatchChange,
  WatchRequest,
} from './types'

/** So viele Titel holt eine Seite. Bei 60 Postern bleibt der erste Aufbau schnell. */
export const LIBRARY_PAGE_SIZE = 60

/** S6: So oft fragt die Titelseite nach, solange ein Serienordner eingelesen wird. */
export const FOLDER_READ_POLL_MS = 2000

/** Nach so vielen Nachfragen hoert sie auf. Ein grosser Serienordner ist in fuenf Minuten gelesen. */
export const FOLDER_READ_POLL_MAX = 150

export const LIBRARY_SORTS: readonly LibrarySort[] = ['title', 'year', 'added']

export type LibraryQuery = {
  kind: MediaKind
  /** null heisst alle Zustaende. "unclear" ist kein Zustand, sondern Serien mit unklaren Dateien. */
  state: VersionState | 'unclear' | null
  q: string
  sort: LibrarySort
  page: number
  /** Seit T1: nur Titel mit diesem Tag. */
  tag?: string | null
}

/**
 * Die Adresse einer Seite der Bibliothek. Suchen, filtern und sortieren tut der
 * Server, er kennt Umlaute und Akzente ("schoene" findet "Schöne"). Die Seite
 * schickt den Text nur ohne Leerraum am Rand und laesst leere Filter weg.
 */
export function libraryPath(query: LibraryQuery): string {
  const params = new URLSearchParams({ kind: query.kind })
  if (query.state !== null) params.set('state', query.state)
  const q = query.q.trim()
  if (q !== '') params.set('q', q)
  if (query.tag) params.set('tag', query.tag)
  params.set('sort', query.sort)
  params.set('page', String(query.page))
  params.set('page_size', String(LIBRARY_PAGE_SIZE))
  return `/library?${params.toString()}`
}

export const libraryApi = {
  list: (query: LibraryQuery, signal?: AbortSignal) => api.get<LibraryPage>(libraryPath(query), { signal }),
  stats: () => api.get<LibraryStats>('/library/stats'),
  detail: (id: string, signal?: AbortSignal) => api.get<TitleDetail>(`/library/${encodeURIComponent(id)}`, { signal }),
  /** Einen Film von TMDB anlegen. 409 `title_exists {title_id}`, 422 `version_kind_mismatch`, TMDB-Fehler. */
  add: (body: LibraryAdd) => api.post<TitleDetail>('/library', body),
  /** Fassungen dazunehmen oder eigene entfernen. 409 `version_owned_by_source`; 409 `version_download_active` ohne `remove_downloads`. */
  changeVersions: (id: number, change: TitleVersionChange) => api.patch<TitleDetail>(`/library/${id}/versions`, change),
  /**
   * 409 `title_has_source_versions`, solange eine Verbindung zu Radarr Fassungen liefert. Laeuft ein Download, 409
   * `title_download_active`, ausser mit `removeDownloads`: Dann entfernt der Server ihn auch im Download-Programm.
   */
  remove: (id: number, removeDownloads = false) => api.delete<void>(removeDownloads ? `/library/${id}?remove_downloads=true` : `/library/${id}`),
  /** Mehrere Titel auf einmal entfernen, samt laufender Downloads; mit `delete_files` die Dateien in den Papierkorb. */
  removeMany: (body: { kind: MediaKind; title_ids?: number[] | null; state?: string | null; q?: string | null; tag?: string | null; delete_files: boolean }) =>
    api.post<RemovedMany>('/library/remove', body),
  /** Series (S1): what adding would watch, per version. Nothing is stored. */
  previewSeries: (body: SeriesAdd, signal?: AbortSignal) => api.post<SeriesPreview>('/library/preview', body, { signal }),
  /** The episodes of one season with their switch, state and file per version. */
  season: (titleId: number, seasonId: number, signal?: AbortSignal) => api.get<SeasonEpisodes>(`/library/${titleId}/seasons/${seasonId}`, { signal }),
  /** What a new rule would change. 409 `version_fed_by_source`. */
  watchPreview: (titleId: number, versionId: number, body: WatchRequest, signal?: AbortSignal) =>
    api.post<WatchChange>(`/library/${titleId}/versions/${versionId}/watch/preview`, body, { signal }),
  /** Apply a rule to a series version. 409 `version_fed_by_source`. */
  changeWatch: (titleId: number, versionId: number, body: WatchRequest) => api.put<TitleDetail>(`/library/${titleId}/versions/${versionId}/watch`, body),
  switchSeason: (titleId: number, seasonId: number, body: SwitchRequest) => api.put<SeasonEpisodes>(`/library/${titleId}/seasons/${seasonId}/watch`, body),
  switchEpisode: (titleId: number, episodeId: number, body: SwitchRequest) => api.put<SeasonEpisodes>(`/library/${titleId}/episodes/${episodeId}/watch`, body),
  /** Switch on every episode TMDB added late, in every version of nexcrate's own. */
  watchLate: (titleId: number) => api.post<TitleDetail>(`/library/${titleId}/late/watch`),
  changeSeriesType: (titleId: number, seriesType: SeriesType) => api.patch<TitleDetail>(`/library/${titleId}`, { series_type: seriesType }),
  /**
   * B1: die Art fuer mehrere Serien auf einmal. Ohne `title_ids` gilt die ganze Ansicht. `fed` sind die Serien, die
   * eine Sonarr-Verbindung fuellt; die bleiben aussen vor.
   */
  setSeriesTypeAll: (body: { series_type: SeriesType; state?: string | null; q?: string | null; tag?: string | null; title_ids?: number[] | null }) =>
    api.put<{ changed: number; fed: number }>('/library/series-type', body),
  /** Anime B5: die Serien, deren Art von TMDBs Vorschlag abweicht, und der Stand des Laufs. */
  seriesTypeProposals: () => api.get<SeriesTypeProposals>('/library/series-type/proposals'),
  /** Fragt TMDB je Serie; ohne `everything` nur die ohne Vorschlag. 409 `series_type_run_running`. */
  runSeriesTypeProposals: (everything: boolean) => api.post<SeriesTypeRun>('/library/series-type/proposals/run', { everything }),
  /**
   * S6 (Ue3): eine unklare Datei einer eigenen Serienfassung Folgen zuordnen. `versionId` ist die Fassungsdefinition.
   * 409 `episode_has_file`, 409 `version_fed_by_source`, 422 `invalid_input`.
   */
  assignFile: (titleId: number, versionId: number, fileId: number, episodeIds: number[], replace = false) =>
    api.post<TitleDetail>(`/library/${titleId}/versions/${versionId}/files/${fileId}/episodes`, replace ? { episode_ids: episodeIds, replace: true } : { episode_ids: episodeIds }),
  /**
   * /api/v1 V2: Dateien einer Fassung in den Papierkorb, bei Serien auch eine Staffel oder eine Folgendatei. Die
   * Ueberwachung bleibt. 409 `version_fed_by_source`, `download_importing`; 404 `version_not_found`.
   */
  deleteFiles: (titleId: number, body: DeleteFilesRequest) => api.post<DeletedFiles>(`/library/${titleId}/delete-files`, body),
  /** Seit 18.09.2026: eine verknuepfte Datei von ihren Folgen loesen. Sie wird unklar; auf der Platte aendert sich nichts. */
  releaseFile: (titleId: number, versionId: number, fileId: number) =>
    api.delete<TitleDetail>(`/library/${titleId}/versions/${versionId}/files/${fileId}/episodes`),
  /** Seit 18.09.2026: jede Folge der Fassung mit ihrer Datei, fuer "Andere Folge" ueber die Naehe hinaus. */
  episodeChoices: (titleId: number, versionId: number) => api.get<EpisodeChoice[]>(`/library/${titleId}/versions/${versionId}/episode-choices`),
  /** S6: "Keine Folge" und zurueck. 409 `file_has_episodes`. */
  leaveOutFile: (titleId: number, versionId: number, fileId: number, leftOut: boolean) =>
    api.put<TitleDetail>(`/library/${titleId}/versions/${versionId}/files/${fileId}/left-out`, { left_out: leftOut }),
  /** S6: alle Vorschlaege der Stufen 1 und 2 einer Fassung uebernehmen. */
  /** `fromNames`: auch Titel, die nexcrate im Dateinamen fand; erst nach Rueckfrage (18.09.2026). */
  takeFileProposals: (titleId: number, versionId: number, fromNames = false) =>
    api.post<TitleDetail>(`/library/${titleId}/versions/${versionId}/files/proposals${fromNames ? '?from_names=true' : ''}`),
  /** S6 (P1): "Ordner neu einlesen". Antwortet 202; der Fortschritt steht danach in `series.reading`. */
  readFolder: (titleId: number, versionId: number) => api.post<TitleDetail>(`/library/${titleId}/versions/${versionId}/files/read`),
  /** Alle Fassungen einer Ansicht beobachten oder in Ruhe lassen; antwortet, wie viele sich geaendert haben. */
  setMonitoredAll: (body: { monitored: boolean; kind: MediaKind; state?: string | null; q?: string | null; tag?: string | null; title_ids?: number[] | null }) =>
    api.put<{ changed: number }>('/library/monitored', body),
  /** Eine Regel fuer mehrere Serien auf einmal; ohne `title_ids` fuer die ganze Ansicht. */
  setWatchRule: (body: { rule: string; from_season?: number | null; state?: string | null; q?: string | null; tag?: string | null; title_ids?: number[] | null }) =>
    api.put<{ versions: number; watched: number }>('/library/watch-rule', body),
  /** Eine Filmfassung beobachten oder in Ruhe lassen. 409 `version_fed_by_source`. */
  setMonitored: (titleId: number, versionId: number, monitored: boolean) =>
    api.put<TitleDetail>(`/library/${titleId}/versions/${versionId}/monitored`, { monitored }),
}
