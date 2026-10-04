import { api } from './client'
import type { AuthentikSetup, LoginAnswer, Me, OidcConfig, OidcState, RecoveryCodes, SetupStatus, TotpEnrolment, TotpState } from './types'

/** Einrichtung und Anmeldung, wie in the design notes unter "Authentication". */
export const authApi = {
  setupStatus: () => api.get<SetupStatus>('/setup/status'),
  setup: (body: { username: string; password: string; language: string }) => api.post<Me>('/setup', body),
  /** Mit zweitem Faktor kommt statt des Kontos `{ second_factor: true }`; der Code folgt mit `loginCode`. */
  login: (username: string, password: string) => api.post<LoginAnswer>('/auth/login', { username, password }),
  loginCode: (code: string) => api.post<Me>('/auth/login/totp', { code }),
  cancelCode: () => api.post<void>('/auth/login/totp/cancel'),
  /** Beim Start ist 401 die Antwort "niemand angemeldet", kein Verlust der Sitzung. */
  me: () => api.get<Me>('/auth/me', { ignoreSessionLost: true }),
  setLanguage: (language: string) => api.patch<Me>('/auth/me', { language }),
  changePassword: (currentPassword: string, newPassword: string) =>
    api.put<void>('/auth/password', { current_password: currentPassword, new_password: newPassword }),
  logout: () => api.post<void>('/auth/logout'),
  logoutAll: () => api.post<void>('/auth/logout-all'),
}

/** Der zweite Faktor: Codes aus einer App plus Wiederherstellungscodes. */
export const totpApi = {
  state: () => api.get<TotpState>('/auth/totp'),
  begin: () => api.post<TotpEnrolment>('/auth/totp/begin'),
  finish: (code: string, password: string) => api.post<RecoveryCodes>('/auth/totp/confirm', { code, password }),
  disable: (password: string) => api.post<void>('/auth/totp/disable', { password }),
  recovery: (password: string) => api.post<RecoveryCodes>('/auth/totp/recovery', { password }),
}

/** Anmeldung ueber einen Anbieter (OpenID Connect), etwa authentik. */
export const oidcApi = {
  /** Oeffentlich: was die Anmeldeseite zeigt. */
  state: () => api.get<OidcState>('/oidc/state', { ignoreSessionLost: true }),
  config: () => api.get<OidcConfig>('/oidc/config'),
  save: (body: { issuer: string; client_id: string; client_secret: string; provider_name: string }) => api.put<OidcConfig>('/oidc/config', body),
  remove: () => api.delete<void>('/oidc/config'),
  authentik: (url: string, token: string) => api.post<AuthentikSetup>('/oidc/authentik/setup', { url, token }),
  linkStart: (password: string) => api.post<{ url: string }>('/oidc/link/start', { password }),
  unlink: () => api.delete<void>('/oidc/link'),
  passwordLogin: (enabled: boolean) => api.put<OidcConfig>('/oidc/password-login', { enabled }),
}
