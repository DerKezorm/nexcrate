import { api } from './client'
import type { TrashState } from './types'

/** Der Stand der TRaSH Guides, nach dem die Profile gebaut werden. */
export const trashApi = {
  state: () => api.get<TrashState>('/trash'),
  /** Die taegliche Nachfrage bei GitHub ein- oder ausschalten. */
  settings: (updatesEnabled: boolean) => api.put<TrashState>('/trash/settings', { updates_enabled: updatesEnabled }),
  /** Den neueren Stand uebernehmen. 409 `trash_update_breaks {versions}`, 502 `trash_unreachable`. */
  update: () => api.post<TrashState>('/trash/update'),
}
