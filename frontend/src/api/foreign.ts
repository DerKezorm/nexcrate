import { api, request } from './client'
import type { Download, ForeignAdopt, ForeignJobList } from './types'

/** Auftraege in der Kategorie von nexcrate, die kein Download verfolgt (Befund 2 vom 22.09.2026). */
export const foreignApi = {
  list: (signal?: AbortSignal) => api.get<ForeignJobList>('/foreign-jobs', { signal }),
  /** 201. 409 `foreign_same_release`, `foreign_job_failed`, `version_owned_by_source`; 404, wenn er weg ist. */
  adopt: (id: number, body: ForeignAdopt) => api.post<Download>(`/foreign-jobs/${id}/import`, body),
  /** 204. Aus dem Programm, samt Dateien. */
  remove: (id: number) => request<void>('DELETE', `/foreign-jobs/${id}`),
}
