import { api } from './client'
import type { Health } from './types'

export const systemApi = {
  /** Antwortet ohne Anmeldung, mit der Version des laufenden Servers. */
  health: () => api.get<Health>('/health'),
}
