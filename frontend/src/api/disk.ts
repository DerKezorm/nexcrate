import { api } from './client'
import type {
  DiskAssignManyRequest,
  DiskAssignRequest,
  DiskFolder,
  DiskFolderList,
  DiskFolderState,
  DiskJob,
  DiskMediaResult,
  DiskOverview,
  DiskRestoreRequest,
  DiskRoot,
  DiskRootKind,
  TitleDetail,
  WatchRule,
} from './types'

/** How often the page asks for a running job while it is open. */
export const DISK_POLL_MS = 1000

/** Rows per request of the folder list. A collection of thousands of folders stays readable page by page. */
export const DISK_PAGE_SIZE = 50

/**
 * `POST /api/disk/folders/{id}/assign` for a series folder (P2): the series, the version
 * definition and what it watches. `file` is not used; the files come from reading the folder afterwards.
 */
export type DiskSeriesAssignRequest = {
  tmdb_id: number
  version_id: number
  rule: WatchRule
  from_season?: number | null
  /** 24.09.2026: die Folgen, die das Einlesen findet, werden nicht verbessert. */
  keep_as_is?: boolean
}

export type DiskFolderQuery = {
  state: DiskFolderState
  /** null: every root. */
  rootId: number | null
  q: string
  offset: number
  limit?: number
  /** Only rows below roots of this kind (S6). Omitted: every row. */
  kind?: DiskRootKind
}

/** The address of one page of the folder list. An empty search text and "every root" are left out. */
export function diskFoldersPath({ state, rootId, q, offset, limit = DISK_PAGE_SIZE, kind }: DiskFolderQuery): string {
  const params = new URLSearchParams({ state })
  if (kind !== undefined) params.set('kind', kind)
  if (rootId !== null) params.set('root_id', String(rootId))
  const text = q.trim()
  if (text !== '') params.set('q', text)
  params.set('offset', String(offset))
  params.set('limit', String(limit))
  return `/disk/folders?${params.toString()}`
}

/**
 * Folders on disk, as in the design notes under "API" and the design notes under "P2". The
 * scan, restoring and assigning many at once run as jobs on the server, at most one at a time (409
 * `disk_job_running`). Nothing here moves, renames or deletes a file; the only file nexcrate writes is `release.nex`.
 */
export const diskApi = {
  /** Without `kind` every root, movie and series ones alike. */
  overview: (signal?: AbortSignal) => api.get<DiskOverview>('/disk', { signal }),
  /** 201 with the root. 404 `folder_not_visible`, 409 `root_exists`. */
  addRoot: (path: string, kind: DiskRootKind = 'movie') => api.post<DiskRoot>('/disk/roots', { path, kind }),
  /** 204. 409 `root_not_removable` for a root that is not the owner's. */
  removeRoot: (id: number) => api.delete<void>(`/disk/roots/${id}`),
  /** 202 with a `DiskJob` of kind `scan`. Without ids every root is scanned. */
  scan: (rootIds?: number[]) => api.post<DiskJob>('/disk/scan', rootIds && rootIds.length > 0 ? { root_ids: rootIds } : {}),
  /** The newest job of the last 30 minutes, else 404 `not_found`. */
  job: () => api.get<DiskJob>('/disk/scan'),
  folders: (query: DiskFolderQuery, signal?: AbortSignal) => api.get<DiskFolderList>(diskFoldersPath(query), { signal }),
  ignore: (id: number) => api.post<DiskFolder>(`/disk/folders/${id}/ignore`),
  unignore: (id: number) => api.delete<DiskFolder>(`/disk/folders/${id}/ignore`),
  /** Reads the media data of one video of the folder. 404 `not_found`, 409 `folder_changed`. */
  media: (id: number, file: string) => api.post<DiskMediaResult>(`/disk/folders/${id}/media`, { file }),
  /**
   * 201 with the title detail. 409 `folder_changed`, `folder_known`, `version_has_file {location}`,
   * `version_owned_by_source`; 422 `version_kind_mismatch`, `invalid_input`; TMDB's codes.
   */
  assign: (id: number, body: DiskAssignRequest) => api.post<TitleDetail>(`/disk/folders/${id}/assign`, body),
  /**
   * The same route for a series folder: 201 with the title detail, then nexcrate reads the folder in the background.
   * 409 `folder_known`, `version_has_files`, `version_owned_by_source`; 422 `version_kind_mismatch`; TMDB's codes.
   */
  assignSeries: (id: number, body: DiskSeriesAssignRequest) => api.post<TitleDetail>(`/disk/folders/${id}/assign`, body),
  /** For a `moved` row: the version takes the new path. 200 with the title detail; 409 `folder_changed`. */
  takePath: (id: number) => api.post<TitleDetail>(`/disk/folders/${id}/path`),
  /** Musik M6: einen Albumordner einem Album der Bibliothek geben; nexcrate liest ihn danach ein. */
  assignAlbum: (id: number, titleId: number) => api.post<TitleDetail>(`/disk/folders/${id}/album`, { title_id: titleId }),
  /** 202 with a `DiskJob` of kind `restore`; 409 `disk_job_running`. */
  restore: (body: DiskRestoreRequest) => api.post<DiskJob>('/disk/restore', body),
  /** 202 with a `DiskJob` of kind `assign`; only rows with an unambiguous proposal, the others count as `skipped`. */
  assignMany: (body: DiskAssignManyRequest) => api.post<DiskJob>('/disk/assign', body),
}
