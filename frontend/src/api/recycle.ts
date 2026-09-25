import { api } from './client'
import type { Recycle, RecycleBin } from './types'

export const RECYCLE_DAYS_MIN = 1
export const RECYCLE_DAYS_MAX = 365

/** Wie viele Tage ersetzte Dateien in `.nexcrate-recycle` bleiben, bevor nexcrate sie loescht. */
export const recycleApi = {
  get: () => api.get<Recycle>('/recycle'),
  /** 422 `invalid_input` ausserhalb von 1 bis 365. */
  save: (days: number) => api.put<Recycle>('/recycle', { days }),
  /** Die geloeschten Dateien, neueste zuerst. */
  bin: (signal?: AbortSignal) => api.get<RecycleBin>('/recycle/bin', { signal }),
  /** 409 `recycle_target_taken`, `recycle_slot_taken`, `recycle_title_gone`, `recycle_file_gone`. */
  restore: (id: number) => api.post<{ title_id: number; kind: string }>(`/recycle/bin/${id}/restore`),
  /** Endgueltig, sofort. */
  purge: (id: number) => api.delete<void>(`/recycle/bin/${id}`),
  empty: () => api.delete<{ deleted: number }>('/recycle/bin'),
}
