import { api } from './client'
import type { AnimeFromTrash, AnimeTrashFamily, CustomFormat, ExpertProfile, ExpertProfileBody, FormatConditionList, FormatSpecification, FormatTestResult, MediaKind, QualityList, QualitySizeRow } from './types'

/** Die Arten, die ein Profil von Hand kennen: Musik hat andere Qualitaeten und kommt spaeter. */
export const EXPERT_KINDS: readonly MediaKind[] = ['movie', 'series']

/**
 * Drei Orte statt einem Topf: die Groessen je Qualitaet gelten fuer alle
 * Profile einer Art, die Formate stehen fuer sich, und das Profil verweist mit Punkten darauf.
 */
export const expertApi = {
  quality: (kind: MediaKind) => api.get<QualityList>(`/quality?kind=${encodeURIComponent(kind)}`),
  /** Nur die genannten Qualitaeten aendern sich. 422 `invalid_input`, wenn das Hoechste unter dem Mindesten liegt. */
  saveQuality: (kind: MediaKind, items: QualitySizeRow[]) => api.put<QualityList>('/quality', { kind, items }),

  formats: (kind: MediaKind) => api.get<CustomFormat[]>(`/custom-formats?kind=${encodeURIComponent(kind)}`),
  conditions: (kind: MediaKind) => api.get<FormatConditionList>(`/custom-formats/conditions?kind=${encodeURIComponent(kind)}`),
  addFormat: (kind: MediaKind, name: string, specifications: FormatSpecification[]) => api.post<CustomFormat>('/custom-formats', { kind, name, specifications }),
  /** Ein Format der Leitfaeden wird dabei zu einem eigenen. 409 `custom_format_name_taken`. */
  changeFormat: (id: number, body: { name?: string; specifications?: FormatSpecification[] }) => api.put<CustomFormat>(`/custom-formats/${id}`, body),
  /** Speichert nichts: die Bedingungen, wie der Dialog sie gerade haelt, gegen einen Release-Namen. */
  testFormat: (kind: MediaKind, name: string, specifications: FormatSpecification[]) =>
    api.post<FormatTestResult>('/custom-formats/test', { kind, name, specifications }),
  /** 409 `custom_format_in_use {versions}`, solange ein Profil darauf zeigt. */
  removeFormat: (id: number) => api.delete<void>(`/custom-formats/${id}`),

  /** 409 `profile_missing`, solange die Fassung kein Profil hat. */
  profile: (versionId: number) => api.get<ExpertProfile>(`/versions/${versionId}/profile/expert`),
  /** Dasselbe am Profil selbst; ein leeres antwortet mit der blanken Qualitaetenliste seiner Art. */
  ofProfile: (profileId: number) => api.get<ExpertProfile>(`/profiles/${profileId}/expert`),
  saveByProfile: (profileId: number, body: ExpertProfileBody) => api.put<ExpertProfile>(`/profiles/${profileId}/expert`, body),
  /** Anime B2b: fuellt den Anime-Zweig aus TRaSH und legt fehlende Formate an. Speichert das Profil nicht. */
  animeFromTrash: (profileId: number, family: AnimeTrashFamily | null) =>
    api.post<AnimeFromTrash>(`/profiles/${profileId}/expert/anime-from-trash`, family === null ? {} : { family }),
  /** Speichern stellt das Profil auf `expert`; der Assistent ueberschreibt es beim naechsten Speichern wieder. */
  saveProfile: (versionId: number, body: ExpertProfileBody) => api.put<ExpertProfile>(`/versions/${versionId}/profile/expert`, body),
}
