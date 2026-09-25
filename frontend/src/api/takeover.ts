import { api } from './client'
import type { ImportRun, TakeoverCheckRequest, TakeoverJob, TakeoverRequest, TakeoverUndoRequest } from './types'

/** So oft fragt das Fenster einen laufenden Auftrag nach, solange es offen ist. */
export const TAKEOVER_POLL_MS = 1000

/**
 * Die Uebernahme einer Verbindung zu Radarr oder Sonarr (S6): erst pruefen, dann uebernehmen. Beides laeuft beim
 * Server im Hintergrund und antwortet 202 mit dem Auftrag. Die App wird dabei nur gelesen. Die Formen sind fuer beide
 * gleich; das Ergebnis einer Sonarr-Verbindung traegt `app` `sonarr` und die Zahlen der Serien.
 */
export const takeoverApi = {
  /**
   * Prueft, was eine Uebernahme taete, und aendert nichts. Ohne Koerper ordnet nexcrate die Ordner selbst zu.
   * 404 `not_found`, 409 `source_taken_over`, `import_running`, `takeover_running`, 422 `takeover_mapping_invalid {remote}`.
   */
  check: (sourceId: number, body?: TakeoverCheckRequest) => api.post<TakeoverJob>(`/sources/${sourceId}/takeover/check`, body),
  /** Dieselben Codes, dazu die von `PUT /api/versions/{id}/folder` fuer `folder`. */
  start: (sourceId: number, body: TakeoverRequest) => api.post<TakeoverJob>(`/sources/${sourceId}/takeover`, body),
  /**
   * Der neueste Auftrag der Verbindung, laufend oder vor weniger als 30 Minuten beendet, sonst null (seit 18.09.2026,
   * vorher 404). 404 `not_found` nur fuer eine unbekannte Verbindung.
   */
  job: (sourceId: number) => api.get<TakeoverJob | null>(`/sources/${sourceId}/takeover`),
  /**
   * Macht eine Uebernahme rueckgaengig (Befund 12): nexcrate liest die Verbindung wieder, und der Server startet gleich
   * einen Import. 202 mit diesem `ImportRun`, wie `sourcesApi.startImport`. 404 `not_found`, 409 `source_not_taken_over`,
   * `import_running`, `takeover_running`, `takeover_downloads_active {count}`, 422 `source_key_missing`, `invalid_input`,
   * dazu Radarrs Codes fuer 502 und 504.
   */
  undo: (sourceId: number, body: TakeoverUndoRequest) => api.post<ImportRun>(`/sources/${sourceId}/takeover/undo`, body),
}
