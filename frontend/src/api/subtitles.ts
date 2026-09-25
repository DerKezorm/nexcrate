import { api } from './client'
import type { SubtitlesState } from './types'

/** Ob nexcrate Untertitel-Dateien aus dem Download neben den Film legt, wie in the design notes unter C9. Ab Werk an. */
export const subtitlesApi = {
  get: () => api.get<SubtitlesState>('/subtitles'),
  save: (enabled: boolean) => api.put<SubtitlesState>('/subtitles', { enabled }),
}
