import { api, API_BASE } from './client'
import type { MediaKind, Profile, ProfileAnswers, ProfileBrief, ProfileImport, ProfileImportRequest, ProfilePreview, ProfilePreviewRequest, QuestionList } from './types'

/** Groesser nimmt der Server eine Profildatei nicht an. Die Seite prueft es vorher. */
export const PROFILE_IMPORT_MAX_BYTES = 64 * 1024

/** Die Arten, fuer die es schon Profile gibt. Eine neue Art braucht Fragen am Server und Texte, keinen neuen Assistenten. */
export const PROFILE_KINDS: readonly MediaKind[] = ['movie', 'series']

/** Die Adresse der YAML-Datei eines Profils, als gewoehnlicher Download-Link. */
export function profileExportUrl(versionId: number): string {
  return `${API_BASE}/versions/${versionId}/profile/export`
}

/** Dieselbe Datei, am Profil selbst. */
export function profileFileUrl(profileId: number): string {
  return `${API_BASE}/profiles/${profileId}/export`
}

/** Ein Profil je Fassung, wie in the design notes unter "API". */
export const profilesApi = {
  /** 422 `profile_kind_unsupported` fuer Arten ohne Fragen. */
  questions: (kind: MediaKind) => api.get<QuestionList>(`/profiles/questions?kind=${encodeURIComponent(kind)}`),
  /** Baut das Profil, ohne zu speichern. 422 `profile_answers_invalid {fields}`. */
  preview: (body: ProfilePreviewRequest) => api.post<ProfilePreview>('/profiles/preview', body),
  /** 404 `not_found`, solange die Fassung kein Profil hat. */
  get: (versionId: number) => api.get<Profile>(`/versions/${versionId}/profile`),
  /** Anlegen und aendern. 422 `profile_answers_invalid {fields}`. */
  save: (versionId: number, answers: ProfileAnswers) => api.put<Profile>(`/versions/${versionId}/profile`, { answers }),
  remove: (versionId: number) => api.delete<void>(`/versions/${versionId}/profile`),
  /** Liest und prueft eine Datei, speichert nichts. 422 `profile_import_invalid {reason}`. */
  importFile: (body: ProfileImportRequest) => api.post<ProfileImport>('/profiles/import', body),

  /** Ein Profil fuer sich: Antworten und Zusammenfassung. Ein leeres hat keine Zusammenfassung. */
  readProfile: (id: number) => api.get<Profile>(`/profiles/${id}`),
  /** Der Assistent auf dem Profil selbst. ⚠️ Nimmt es dem Expertenmodus wieder ab. */
  saveProfile: (id: number, answers: ProfileAnswers) => api.put<Profile>(`/profiles/${id}`, { answers }),

  /** Die Profile als eigene Dinge: eine Fassung zeigt auf eines, mehrere duerfen auf dasselbe zeigen. */
  list: (kind?: MediaKind) => api.get<ProfileBrief[]>(kind === undefined ? '/profiles' : `/profiles?kind=${encodeURIComponent(kind)}`),
  /** Leer oder als Kopie eines anderen. 409 `profile_name_taken`. */
  add: (body: { name: string; kind: MediaKind; copy_of?: number }) => api.post<ProfileBrief>('/profiles', body),
  rename: (id: number, name: string) => api.patch<ProfileBrief>(`/profiles/${id}`, { name }),
  /** 409 `profile_in_use {versions}`, solange eine Fassung danach urteilt. */
  removeProfile: (id: number) => api.delete<void>(`/profiles/${id}`),
  /** Welches Profil eine Fassung nimmt; `null` nimmt ihr das Profil, ohne es zu entfernen. */
  choose: (versionId: number, profileId: number | null) => api.put<ProfileBrief | null>(`/versions/${versionId}/profile-choice`, { profile_id: profileId }),
}
