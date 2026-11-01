import { api } from './client'
import type { Download, FolderImport, FolderPreview } from './types'

/** Import aus einem Ordner von Hand (Issue #26). Die Dateien bleiben liegen, nexcrate verlinkt oder kopiert sie. */
export const folderImportApi = {
  /** Aendert nichts. 404 `folder_not_visible`, 409 `folder_import_empty`, `folder_import_dangerous`. */
  preview: (path: string) => api.post<FolderPreview>('/folder-import/preview', { path }),
  /** 201. 409 `folder_import_in_library`, `folder_import_running`, `foreign_no_version`, `version_owned_by_source`. */
  start: (body: FolderImport) => api.post<Download>('/folder-import', body),
}
