import { api } from './client'

/** Die Art einer Regel: Filme, Serien oder Musik (dann an Kuenstlern, wie in Lidarr). */
export type AutoTagKind = 'movie' | 'series' | 'album'

export type AutoTagConditionType =
  | 'genre'
  | 'year'
  | 'root_folder'
  | 'runtime'
  | 'keyword'
  | 'studio'
  | 'original_language'
  | 'profile'
  | 'status'
  | 'monitored'
  | 'tag'
  | 'series_type'

/** Eine Bedingung. Welches Feld zaehlt, sagt `type`. */
export type AutoTagCondition = {
  type: AutoTagConditionType
  negate: boolean
  required: boolean
  values?: string[] | null
  min?: number | null
  max?: number | null
  value?: string | null
  profile_id?: number | null
}

export type AutoTag = {
  id: number
  kind: AutoTagKind
  name: string
  tags: string[]
  remove_automatically: boolean
  conditions: AutoTagCondition[]
}

export type AutoTagInput = Omit<AutoTag, 'id'>

export type AutoTagOptions = {
  conditions: AutoTagConditionType[]
  statuses: string[]
  series_types: string[]
  genres: string[]
  languages: string[]
  root_folders: string[]
  profiles: { id: number; name: string }[]
}

/** Warum eine Regel aus einer App nicht mitkommt, oder was an ihr wegfaellt. */
export type AutoTagRuleNote = { code: string; value: string }

/** Eine Regel aus Radarr, Sonarr oder Lidarr, schon in nexcrates Feldern. */
export type SourceAutoTag = {
  name: string
  tags: string[]
  remove_automatically: boolean
  conditions: AutoTagCondition[]
  importable: boolean
  exists: boolean
  problems: AutoTagRuleNote[]
  dropped: AutoTagRuleNote[]
}

export type SourceAutoTags = { app: 'radarr' | 'sonarr' | 'lidarr'; kind: AutoTagKind; rules: SourceAutoTag[] }

export const autoTagsApi = {
  /** Liest die Regeln einer Verbindung; gespeichert wird nichts, uebernommen wird ueber `create`. */
  fromSource: (sourceId: number) => api.get<SourceAutoTags>(`/sources/${sourceId}/auto-tags`),
  list: (kind: AutoTagKind) => api.get<AutoTag[]>(`/auto-tags?kind=${kind}`),
  options: (kind: AutoTagKind) => api.get<AutoTagOptions>(`/auto-tags/options?kind=${kind}`),
  create: (body: AutoTagInput) => api.post<{ rule: AutoTag; changed: number }>('/auto-tags', body),
  update: (id: number, body: AutoTagInput) => api.put<{ rule: AutoTag; changed: number }>(`/auto-tags/${id}`, body),
  remove: (id: number) => api.delete<{ changed: number }>(`/auto-tags/${id}`),
}
