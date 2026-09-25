import { api } from './client'
import type { ArrQualitySetup, ArrQualityTaken, ImportRun, MusicSourceNaming, RadarrIndexer, SeriesSourceNaming, Source, SourceCreate, SourceDelay, SourceNaming, SourceTest, SourceTestResult, SourceUpdate } from './types'

/**
 * Radarr- und Sonarr-Instanzen, nur lesend. Der API-Schluessel geht nur hinaus, nie herein:
 * Antworten tragen `has_api_key`. Die Seite haelt ihn nur, solange das Formular offen ist.
 * Eine uebernommene Verbindung (`taken_over_at`) antwortet auf alles ausser Lesen, Entfernen und `takeoverApi.undo`
 * 409 `source_taken_over`.
 */
export const sourcesApi = {
  list: () => api.get<Source[]>('/sources'),
  /** 409 `version_taken`, wenn die Fassung schon aus einer anderen Quelle kommt. */
  create: (body: SourceCreate) => api.post<Source>('/sources', body),
  update: (id: number, body: SourceUpdate) => api.patch<Source>(`/sources/${id}`, body),
  /**
   * Entfernt die aus ihr uebernommenen Fassungen, in Radarr aendert sich nichts. Bei einer uebernommenen Verbindung
   * nur den Eintrag: Filme, Fassungen und Dateien bleiben.
   */
  remove: (id: number) => api.delete<void>(`/sources/${id}`),
  test: (body: SourceTest) => api.post<SourceTestResult>('/sources/test', body),
  startImport: (id: number) => api.post<ImportRun>(`/sources/${id}/import`),
  /**
   * Die Indexer, die in diesem Radarr oder Sonarr stehen. Uebernommen wird ueber `indexersApi.fromSource`. Bei Sonarr
   * sind `categories` die Serienkategorien.
   */
  indexers: (id: number) => api.get<RadarrIndexer[]>(`/sources/${id}/indexers`),
  /**
   * Die Qualitaets-Einstellungen einer Radarr- oder Sonarr-Verbindung: Profile, Custom Formats und Groessen, und
   * was daraus hier wuerde. Liest nur. 409 `source_app_unsupported` bei Lidarr.
   */
  qualitySetup: (id: number) => api.get<ArrQualitySetup>(`/sources/${id}/quality-setup`),
  /** Uebernimmt die genannten Profile samt Formaten und Groessen. */
  takeQualitySetup: (id: number, profiles: string[], addRepack = false) =>
    api.post<ArrQualityTaken>(`/sources/${id}/quality-setup`, { profiles, add_repack: addRepack }),
  /** Radarrs Benennung, gespeichert wird nichts. Uebernommen wird ueber `namingApi.saveVersion`. 422 `source_key_missing`. */
  naming: (id: number) => api.get<SourceNaming>(`/sources/${id}/naming`),
  /**
   * Dieselbe Route fuer eine Sonarr-Verbindung (S6, Entscheidung 13): die fuenf Serienmuster. Gespeichert wird nichts.
   * Uebernommen wird ueber `namingApi.saveSeriesVersion`.
   */
  seriesNaming: (id: number) => api.get<SeriesSourceNaming>(`/sources/${id}/naming`),
  /**
   * Das Delay-Profil ohne Tag der Verbindung als Verzoegerungsregel. Gespeichert wird nichts; uebernommen wird ueber
   * `versionsApi.setDelay`. Auch eine uebernommene Verbindung antwortet.
   */
  delay: (id: number) => api.get<SourceDelay>(`/sources/${id}/delay`),
  /** Lidarrs Benennung derselben Route. Uebernommen wird ueber `namingApi.save` mit `music`. */
  musicNaming: (id: number) => api.get<MusicSourceNaming>(`/sources/${id}/naming`),
}
