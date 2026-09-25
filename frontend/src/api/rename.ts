import { api } from './client'

/** Massenumbenennung. Musik rechnet je Kuenstler, Filme und Serien je Titel. */
export type RenameKind = 'movie' | 'series' | 'music'

export type RenameStep = { what: 'file' | 'extra' | 'other'; old: string; new: string }

export type RenameNote = { code: string; values: Record<string, unknown> }

export type RenameUnit = {
  /** Titel bei Filmen und Serien, Kuenstler bei Musik. */
  id: number
  kind: RenameKind
  title_id: number
  name: string
  files: number
  moves: number
  folders: { old: string; new: string }[]
  steps: RenameStep[]
  more: number
  notes: RenameNote[]
  skip: string | null
  skip_values: Record<string, unknown>
  left: number
  albums: { title_id: number; folder: string; files: number }[]
}

export type RenameCounts = { titles: number; files: number; moves: number; folders: number; skipped: number }

export type RenamePreview = { kind: RenameKind; computed_at: string | null; counts: RenameCounts | null; units: RenameUnit[] }

export type RenameJob = {
  id: string
  action: 'preview' | 'run' | 'undo'
  kind: RenameKind
  state: 'running' | 'done' | 'failed'
  done: number
  total: number
  started_at: string
  finished_at: string | null
  run_id: number | null
  result: Record<string, unknown>
  error: string | null
}

export type RenameLastRun = {
  id: number
  kind: RenameKind
  state: 'done' | 'undone'
  started_at: string
  finished_at: string | null
  undone_at: string | null
  counts: Record<string, unknown>
  can_undo: boolean
}

/** So oft fragt die Seite einen laufenden Auftrag nach. */
export const RENAME_POLL_MS = 1000

export const renameApi = {
  preview: (kind: RenameKind) => api.get<RenamePreview>(`/rename/preview?kind=${kind}`),
  computePreview: (kind: RenameKind) => api.post<RenameJob>('/rename/preview', { kind }),
  unit: (kind: RenameKind, id: number) => api.get<RenameUnit>(`/rename/preview/${kind}/${id}`),
  run: (kind: RenameKind, ids: number[] | null) => api.post<RenameJob>('/rename/run', { kind, ids }),
  job: () => api.get<RenameJob | null>('/rename/job'),
  last: () => api.get<RenameLastRun | null>('/rename/last'),
  undo: () => api.post<RenameJob>('/rename/last/undo'),
  titlePreview: (titleId: number) => api.get<RenameUnit | null>(`/rename/titles/${titleId}`),
  renameTitle: (titleId: number) => api.post<RenameJob>(`/rename/titles/${titleId}`),
}

/** Zahlen aus dem Ergebnis eines Auftrags, fehlende als 0. */
export function resultNumber(job: RenameJob | null, key: string): number {
  const value = job?.result[key]
  return typeof value === 'number' ? value : 0
}

/** Die Gruende aus dem Ergebnis eines Laufs oder Rueckgaengig, als Code und Zahl. */
export function resultReasons(job: RenameJob | null): [string, number][] {
  const reasons = job?.result.reasons
  if (reasons === null || typeof reasons !== 'object') return []
  return Object.entries(reasons as Record<string, unknown>).filter((entry): entry is [string, number] => typeof entry[1] === 'number')
}
