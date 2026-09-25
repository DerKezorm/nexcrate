import { api } from './client'
import type { DelayWithTags, FolderRule, MediaKind, Relocation, Version, VersionCreate, VersionUpdate } from './types'

/** Fassungen je Medienart, wie in the design notes unter "Versions". In Schritt 1 nur ein Name. */
export const versionsApi = {
  list: (kind: MediaKind) => api.get<Version[]>(`/versions?kind=${kind}`),
  /** 409 `version_label_taken`, wenn es den Namen fuer diese Art schon gibt. */
  create: (body: VersionCreate) => api.post<Version>('/versions', body),
  /** Umbenennen. 409 `version_label_taken`. */
  update: (id: number, body: VersionUpdate) => api.patch<Version>(`/versions/${id}`, body),
  /** 409 `version_in_use {sources, titles}`, solange eine Verbindung oder ein Titel sie nutzt. */
  remove: (id: number) => api.delete<void>(`/versions/${id}`),
  /**
   * Seit Schritt 3: der Ordner einer Fassung fuer Filme, oder null ohne Ordner. Verschiebt nichts.
   * 404 `folder_not_visible`, 422 `folder_not_writable`, 409 `folder_in_use {label}`.
   */
  setFolder: (id: number, folder: string | null) => api.put<Version>(`/versions/${id}/folder`, { folder }),
  /**
   * Die Verzoegerungsregel der Fassung. 422 `invalid_input {fields}`, etwa wenn beide Protokolle aus sind. Was schon
   * wartet, bekommt seinen Zeitpunkt neu.
   */
  setDelay: (id: number, rule: DelayWithTags) => api.put<Version>(`/versions/${id}/delay`, rule),
  /** Regeln fuer Mediatheken: die erste passende bestimmt den Ordner eines neuen Titels. */
  folderRules: (id: number) => api.get<{ rules: FolderRule[] }>(`/versions/${id}/folder-rules`),
  /** 422 `invalid_input {fields}` oder `folder_rules_not_for_kind`, dazu die Fehler der Ordnerwahl. */
  setFolderRules: (id: number, rules: FolderRule[]) => api.put<{ rules: FolderRule[] }>(`/versions/${id}/folder-rules`, { rules }),
  /** Vorschau: welche Titel die Regeln woanders haetten, mit Grund, wenn einer nicht umziehen kann. */
  relocations: (id: number) => api.get<Relocation[]>(`/versions/${id}/relocations`),
  /** Zieht die Titel je als ganzen Ordner um; wer nicht kann, steht in `left`. */
  relocate: (id: number, titleIds: number[]) => api.post<{ moved: number[]; left: { title_id: number; skip: string }[] }>(`/versions/${id}/relocate`, { title_ids: titleIds }),
}
