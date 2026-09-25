import { api } from './client'

/** Ein Tag mit dem, was ihn traegt. */
export type TagEntry = { id: number; label: string; movie: number; series: number; artist: number }

/** Mehrere auf einmal: die markierten Nummern, oder ohne sie die ganze Ansicht (Art, Zustand, Suche, Tag). */
export type TagsChange = {
  kind: 'movie' | 'series' | 'artist'
  add?: string[]
  remove?: string[]
  ids?: number[] | null
  state?: string | null
  q?: string | null
  tag?: string | null
}

export const tagsApi = {
  list: (signal?: AbortSignal) => api.get<{ items: TagEntry[] }>('/tags', { signal }),
  rename: (id: number, label: string) => api.put<TagEntry>(`/tags/${id}`, { label }),
  remove: (id: number) => api.delete<void>(`/tags/${id}`),
  /** Genau diese Tags danach; zurueck kommen sie so, wie der Server sie speichert (klein, sortiert). */
  setTitle: (titleId: number, tags: string[]) => api.put<{ tags: string[] }>(`/library/${titleId}/tags`, { tags }),
  setArtist: (artistId: number, tags: string[]) => api.put<{ tags: string[] }>(`/music/artists/${artistId}/tags`, { tags }),
  change: (body: TagsChange) => api.post<{ changed: number }>('/tags/change', body),
}
