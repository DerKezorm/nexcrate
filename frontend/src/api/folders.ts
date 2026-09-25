import { api } from './client'
import type { FolderListing, FolderMounts } from './types'

/**
 * Nur Ordner, die nexcrate selbst sieht. Die Oberflaeche laesst nie einen Pfad eintippen: Sie
 * zeigt die eingebundenen Ordner und blaettert von dort aus.
 */
export const foldersApi = {
  mounts: () => api.get<FolderMounts>('/folders'),
  /** 404 `folder_not_visible` fuer einen Pfad ausserhalb der eingebundenen Ordner. */
  list: (path: string) => api.get<FolderListing>(`/folders?path=${encodeURIComponent(path)}`),
}
