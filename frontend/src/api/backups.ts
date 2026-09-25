import { api, downloadFile } from './client'

export type BackupKind = 'manual' | 'scheduled' | 'update'
export type BackupSchedule = 'off' | 'daily' | 'weekly' | 'monthly'

export const BACKUP_SCHEDULES: readonly BackupSchedule[] = ['off', 'daily', 'weekly', 'monthly']

export type Backup = {
  name: string
  size: number
  created: string
  kind: BackupKind
  comment: string
  version: string
  restorable: boolean
  /** `ok`, `backup_newer` oder `unknown_version`. */
  reason: string
}

export type BackupList = {
  entries: Backup[]
  version: string
  folder: string
  schedule: BackupSchedule
  keep: number
}

/** Was ein Archiv ueber sich sagt, bevor es eingespielt wird. */
export type ArchiveInfo = {
  version: string
  created: string
  kind: string
  comment: string
  restorable: boolean
  reason: string
  key_in_archive: boolean
  key_from_environment: boolean
  restarting: boolean
}

function archiveForm(file: File, password: string): FormData {
  const form = new FormData()
  form.append('file', file)
  form.append('password', password)
  return form
}

/** Sicherungen: anlegen, herunterladen, loeschen, ein Archiv pruefen und einspielen. */
export const backupsApi = {
  list: () => api.get<BackupList>('/backups'),
  settings: (body: { schedule?: BackupSchedule; keep?: number }) => api.put<BackupList>('/backups/settings', body),
  /** 201. 500 `backup_failed`. */
  create: (comment: string) => api.post<Backup>('/backups', { comment }),
  /** 404 `backup_not_found`. */
  remove: (name: string) => api.delete<void>(`/backups/${encodeURIComponent(name)}`),
  /** Das verschluesselte Archiv; der Browser speichert es. */
  download: (backup: Backup, password: string) =>
    downloadFile(`/backups/${encodeURIComponent(backup.name)}/archive`, { password }, backup.name.replace(/\.db$/, '.zip')),
  /** Nur nachsehen. 422 `restore_wrong_password`, `restore_not_an_archive`, `restore_not_a_backup` und andere. */
  check: (file: File, password: string) => api.post<ArchiveInfo>('/backups/check', archiveForm(file, password)),
  /** Legt das Archiv bereit; nexcrate startet danach neu und spielt es ein. */
  restore: (file: File, password: string) => api.post<ArchiveInfo>('/backups/restore', archiveForm(file, password)),
  /** Eine Kopie der Liste einspielen, ohne Archiv; der Schluessel bleibt der eigene. 404, 409 `restore_backup_newer`. */
  restoreCopy: (name: string) => api.post<LocalRestore>(`/backups/${encodeURIComponent(name)}/restore`),
}

/** Dasselbe im Einrichtungsassistenten, solange es noch kein Konto gibt (danach 404). */
export const setupBackupApi = {
  check: (file: File, password: string) => api.post<ArchiveInfo>('/setup/backup/check', archiveForm(file, password)),
  restore: (file: File, password: string) => api.post<ArchiveInfo>('/setup/backup/restore', archiveForm(file, password)),
}

export type LocalRestore = { name: string; version: string; created: string; restarting: boolean }
