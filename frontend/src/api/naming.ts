import { api, request } from './client'
import type { EpisodeNumbering, MultiEpisodeStyle, MusicNamingPatterns, Naming, NamingPatterns, NamingPreview, SeriesNamingPatterns, SeriesNamingPreview, SeriesNamingVersion, VersionNaming, VersionNamingPatterns } from './types'

/** Die Muster fuer Filmordner und Filmdatei. 422 `naming_token_unknown {token}`, `naming_pattern_empty`. */
export const namingApi = {
  get: () => api.get<Naming>('/naming'),
  save: (body: NamingPatterns) => api.put<Naming>('/naming', body),
  /** Fuellt die Muster fuer einen erfundenen Film. Eine neuere Vorschau bricht die alte ab. */
  preview: (body: NamingPatterns, signal?: AbortSignal) => request<NamingPreview>('POST', '/naming/preview', { body, signal }),
  /** Eigene Muster fuer eine Fassung. 404 `not_found`, 422 `version_kind_mismatch` und die Codes von `save`. */
  saveVersion: (versionId: number, body: VersionNamingPatterns) => api.put<VersionNaming>(`/naming/versions/${versionId}`, body),
  /** Zurueck zur Standard-Benennung. Antwortet den Eintrag mit `own` false. */
  resetVersion: (versionId: number) => api.delete<VersionNaming>(`/naming/versions/${versionId}`),
  /** Seit S4: Muster fuer erfundene Folgen. */
  seriesPreview: (body: SeriesNamingPatterns & { multi_episode_style: MultiEpisodeStyle; umlauts: string; episode_numbering: EpisodeNumbering }, signal?: AbortSignal) =>
    request<SeriesNamingPreview>('POST', '/naming/series/preview', { body, signal }),
  /** Eigene Muster (null: Vorgabe) und die Nummern im Namen einer Serienfassung. */
  saveSeriesVersion: (versionId: number, body: Record<string, string | null>) => api.put<SeriesNamingVersion>(`/naming/series/versions/${versionId}`, body),
  resetSeriesVersion: (versionId: number) => api.delete<SeriesNamingVersion>(`/naming/series/versions/${versionId}`),
  /** Seit M4: die Muster fuer ein erfundenes Album. */
  musicPreview: (body: MusicNamingPatterns & { umlauts: string }, signal?: AbortSignal) =>
    request<MusicNamingPatterns>('POST', '/naming/music/preview', { body, signal }),
}
