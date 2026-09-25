import { api } from './client'
import type { WaitingPage } from './types'

/**
 * Releases, die die Automatik behaelt statt sie zu laden, bis die Wartezeit ihrer Fassung um ist
 *. Die Regel selbst steht an der Fassung (`versionsApi.setDelay`).
 */
export const waitingApi = {
  /** Was zuerst laden darf, steht oben. Mit `titleId` nur die Releases eines Titels. */
  list: (page = 1, signal?: AbortSignal, titleId?: number) =>
    api.get<WaitingPage>(`/waiting?page=${page}${titleId !== undefined ? `&title_id=${titleId}` : ''}`, { signal }),
  /**
   * Sofort laden, wie ein Klick auf "Laden": ohne Wartezeit, mit allen Pruefungen. 404 `waiting_release_not_found`,
   * sonst die Codes von `POST /api/downloads`.
   */
  load: (id: number) => api.post<{ download_id: number }>(`/waiting/${id}/load`),
  /** 204. Das Release wird nicht gesperrt; eine spaetere Suche darf es wieder finden. */
  discard: (id: number) => api.delete<void>(`/waiting/${id}`),
}
