import { API_BASE, api } from './client'
import type { LogLevel, LogMode, LogModeChange, LogsResponse } from './types'

export const LOG_LEVELS: readonly LogLevel[] = ['DEBUG', 'INFO', 'WARNING', 'ERROR']

export const LOG_LIMITS = [100, 200, 500, 1000] as const

/** Ein gewoehnlicher Link. Den Download uebernimmt der Browser, das Cookie geht mit. */
export const LOG_DOWNLOAD_URL = `${API_BASE}/logs/download`

export const logsApi = {
  read: ({ level, search, limit }: { level: LogLevel; search: string; limit: number }) => {
    const params = new URLSearchParams({ limit: String(limit), level })
    if (search.trim() !== '') params.set('search', search.trim())
    return api.get<LogsResponse>(`/logs?${params.toString()}`)
  },
  setMode: (change: LogModeChange) => api.put<LogMode>('/logs/mode', change),
  clear: () => api.delete<void>('/logs'),
}
