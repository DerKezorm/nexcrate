import { api, request } from './client'
import type { AlbumAssignBody, AlbumFiles, AssignBody, Download, DownloadCreate, DownloadFiles, DownloadList, DownloadRemoval, DownloadsView, LoadBlock, LoadConfirmation, TakesResult } from './types'

/** So oft fragt die Seite Downloads nach, solange sie offen ist. */
export const DOWNLOADS_POLL_MS = 5000

export const DOWNLOADS_PER_PAGE = 50

/** Die Gruende, warum eine Fassung nicht laden kann, in der Reihenfolge des Plans. */
export const LOAD_BLOCKS: readonly LoadBlock[] = ['version_fed_by_source', 'version_no_profile', 'version_no_folder', 'no_client_for_protocol', 'download_active', 'episodes_downloading']

export function isLoadBlock(code: unknown): code is LoadBlock {
  return typeof code === 'string' && (LOAD_BLOCKS as readonly string[]).includes(code)
}

export function downloadsPath(view: DownloadsView, page: number): string {
  const params = new URLSearchParams({ view, page: String(page), per_page: String(DOWNLOADS_PER_PAGE) })
  return `/downloads?${params.toString()}`
}

/** Laden, verfolgen, erneut versuchen, entfernen. Wie in the design notes unter "Downloads". */
export const downloadsApi = {
  list: (view: DownloadsView, page: number, signal?: AbortSignal) => api.get<DownloadList>(downloadsPath(view, page), { signal }),
  /**
   * 201. 404 `search_expired`, `release_not_found`; 409 mit einem Grund aus `LOAD_BLOCKS`,
   * `release_not_fitting`, `release_blocklisted`; 502 `release_fetch_failed`, `release_file_invalid`,
   * `client_refused` und die Codes der Programme.
   */
  load: (body: DownloadCreate) => api.post<Download>('/downloads', body),
  /** 204. 409 `download_finished`, wenn ein abgelegter Download im Programm entfernt werden soll. */
  remove: (id: number, removal: DownloadRemoval) =>
    request<void>('DELETE', `/downloads/${id}?remove_from_client=${String(removal.remove_from_client)}&blocklist=${String(removal.blocklist)}`),
  /** Nur bei `problem` und `completed`, sonst 409 `download_not_retryable`. */
  retry: (id: number) => api.post<Download>(`/downloads/${id}/retry`),
  /** Nimmt einen fehlgeschlagenen Download von den Problemen. Er bleibt im Verlauf. 409 `download_not_failed`. */
  clear: (id: number) => api.post<Download>(`/downloads/${id}/clear`),
  /** Uebernimmt die vorgeschlagene Zuordnung ins Programm und versucht es erneut. 409 `mapping_not_proposed`. */
  confirmMapping: (id: number) => api.post<Download>(`/downloads/${id}/mapping`),
  /** Seit S4: laedt, was eine Seriensuche fuer eine Fassung nehmen wuerde, je Release ein Ergebnis. */
  takes: (body: { search_id: string; version_id: number; confirm: LoadConfirmation[] }) => api.post<TakesResult>('/downloads/takes', body),
  /** Seit S4: die Videos eines Downloads und die Folgen der Serie, fuer "Von Hand zuordnen" und "Datei waehlen". */
  files: (id: number, signal?: AbortSignal) => api.get<DownloadFiles>(`/downloads/${id}/files`, { signal }),
  /** 409 `download_not_assignable`, `assignment_not_better`, `download_files_changed`; 422 `episode_twice`, `episode_not_in_series`. */
  assign: (id: number, body: AssignBody) => api.post<Download>(`/downloads/${id}/assign`, body),
  /** "Rest nicht ablegen". 409 `download_not_assignable`. */
  finish: (id: number) => api.post<Download>(`/downloads/${id}/finish`),
  /** Seit M4: die Audiodateien eines Albendownloads, die Ausgaben mit Titeln und die Titel einer Ausgabe. */
  albumFiles: (id: number, releaseId?: number | null, signal?: AbortSignal) =>
    api.get<AlbumFiles>(`/downloads/${id}/album-files${releaseId ? `?release_id=${releaseId}` : ''}`, { signal }),
  /** 409 `download_not_assignable`, `download_busy`; 422 `track_twice`, `track_not_in_release`, `release_fixed`. */
  albumAssign: (id: number, body: AlbumAssignBody) => api.post<Download>(`/downloads/${id}/album-assign`, body),
  /** Die gewaehlte Datei eines Films mit mehreren Videos. 409 `download_not_choosable`. */
  choose: (id: number, key: number) => api.post<Download>(`/downloads/${id}/choose`, { key }),
}
