import { api } from './client'
import type { Me, SetupStatus } from './types'

/** Einrichtung und Anmeldung, wie in the design notes unter "Authentication". */
export const authApi = {
  setupStatus: () => api.get<SetupStatus>('/setup/status'),
  setup: (body: { username: string; password: string; language: string }) => api.post<Me>('/setup', body),
  login: (username: string, password: string) => api.post<Me>('/auth/login', { username, password }),
  /** Beim Start ist 401 die Antwort "niemand angemeldet", kein Verlust der Sitzung. */
  me: () => api.get<Me>('/auth/me', { ignoreSessionLost: true }),
  setLanguage: (language: string) => api.patch<Me>('/auth/me', { language }),
  changePassword: (currentPassword: string, newPassword: string) =>
    api.put<void>('/auth/password', { current_password: currentPassword, new_password: newPassword }),
  logout: () => api.post<void>('/auth/logout'),
  logoutAll: () => api.post<void>('/auth/logout-all'),
}
