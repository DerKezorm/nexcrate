import { api } from './client'
import type { ImportRun } from './types'

/** So viele Laeufe zeigt die Liste der letzten Uebernahmen. */
export const IMPORT_LIST_LIMIT = 20

/** Wie oft ein laufender Import nachgefragt wird. */
export const IMPORT_POLL_MS = 2000

export const importsApi = {
  list: (limit: number = IMPORT_LIST_LIMIT) => api.get<ImportRun[]>(`/imports?limit=${limit}`),
  get: (id: number) => api.get<ImportRun>(`/imports/${id}`),
}
