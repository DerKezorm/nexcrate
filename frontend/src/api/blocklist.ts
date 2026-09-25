import { api } from './client'
import type { BlocklistEntry, BlocklistPage } from './types'

/** Die Sperrliste eines Titels. Eine Sperre aufheben loescht den Eintrag; das Release zaehlt danach wie jedes andere. */
export const blocklistApi = {
  list: (titleId: number) => api.get<BlocklistEntry[]>(`/blocklist?title_id=${titleId}`),
  /** Alle gesperrten Releases mit ihrem Titel, die neuesten zuerst. */
  all: (page = 1, signal?: AbortSignal) => api.get<BlocklistPage>(`/blocklist/all?page=${page}`, { signal }),
  /** 204. 404 `blocklist_entry_not_found`. */
  remove: (id: number) => api.delete<void>(`/blocklist/${id}`),
}
