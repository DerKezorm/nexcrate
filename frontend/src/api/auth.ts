import { api } from './client'
import type { AuthentikAddress } from '../vendor/nexoidc/oidc'
import type {
  AuthentikResult,
  LoginAnswer,
  Me,
  MyProvider,
  OidcImpact,
  OidcProvider,
  OidcProviderForm,
  OidcState,
  PasswordLogin,
  RecoveryCodes,
  SetupStatus,
  TotpEnrolment,
  TotpState,
} from './types'

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

/** Anmeldung ueber Anbieter (OpenID Connect), gemeinsam mit allen nex-Apps. */
export const oidcApi = {
  /** Oeffentlich: was die Anmeldeseite zeigt. */
  state: () => api.get<OidcState>('/oidc/state', { ignoreSessionLost: true }),
  providers: () => api.get<OidcProvider[]>('/oidc/admin/providers'),
  addProvider: (body: OidcProviderForm) => api.post<OidcProvider>('/oidc/admin/providers', body),
  saveProvider: (id: number, body: OidcProviderForm) => api.put<OidcProvider & { dropped: number }>(`/oidc/admin/providers/${id}`, body),
  removeProvider: (id: number) => api.delete<OidcImpact>(`/oidc/admin/providers/${id}`),
  orderProviders: (ids: number[]) => api.put<unknown>('/oidc/admin/providers/order', { ids }),
  impact: (id: number, issuer = '') =>
    api.get<OidcImpact>(`/oidc/admin/providers/${id}/impact${issuer ? `?issuer=${encodeURIComponent(issuer)}` : ''}`),
  mine: () => api.get<MyProvider[]>('/oidc/me'),
  linkStart: (slug: string, password: string) => api.post<{ url: string }>(`/oidc/${encodeURIComponent(slug)}/link`, { password }),
  unlink: (slug: string) => api.delete<void>(`/oidc/${encodeURIComponent(slug)}/link`),
  /** `publicUrl`: die Adresse, die der Besitzer in der Karte bestaetigt hat, solange nexcrate keine eigene hat. */
  authentik: (url: string, token: string, confirmIssuerChange = false, publicUrl = '') =>
    api.post<AuthentikResult>('/oidc/authentik/setup', {
      url,
      token,
      confirm_issuer_change: confirmIssuerChange,
      public_url: publicUrl,
    }),
  /** Was die Karte ueber die oeffentliche Adresse zeigt (Vorschlag, Warnung, veraltete Rueckleitadresse). */
  authentikAddress: () => api.get<AuthentikAddress>('/oidc/authentik/address'),
  /** "Nur uebernehmen": 409 `public_url_change` fragt zuerst (`count`, `address`, `hand`), 422 `public_url_invalid`. */
  saveAddress: (publicUrl: string, confirm = false) =>
    api.post<AuthentikAddress>('/oidc/authentik/address', { public_url: publicUrl, confirm }),
  passwordLogin: () => api.get<PasswordLogin>('/oidc/password-login'),
  setPasswordLogin: (enabled: boolean) => api.put<PasswordLogin>('/oidc/password-login', { enabled }),
}
