import { api } from './client'
import type { ApiKey, ApiKeyCreated, ApiKeyList, ApiKeyScope } from './types'

/**
 * Die Schluessel, mit denen sich andere Programme an `/api/v1` anmelden. Den Schluessel selbst gibt es genau einmal:
 * in der Antwort von `create`. Die Liste traegt nur seine letzten vier Zeichen.
 */
export const apiKeysApi = {
  list: () => api.get<ApiKeyList>('/api-keys'),
  /** 201. 422 `api_key_name_invalid`, `api_key_scopes_invalid`, 409 `api_key_name_taken`, `api_key_limit`. */
  create: (body: { name: string; scopes: ApiKeyScope[] }) => api.post<ApiKeyCreated>('/api-keys', body),
  /** 404 `api_key_not_found`, 422 `api_key_name_invalid`, 409 `api_key_name_taken`. */
  rename: (id: number, name: string) => api.patch<ApiKey>(`/api-keys/${id}`, { name }),
  /** Der Schluessel gilt sofort nicht mehr. 404 `api_key_not_found`. */
  revoke: (id: number) => api.delete<void>(`/api-keys/${id}`),
}

export interface DeviceCode {
  pairing_id: string
  address: string
  link: string
  qr_svg: string
  expires_at: string
}

export interface DeviceCodeState {
  state: 'waiting' | 'connected' | 'expired'
  key_name: string | null
  expires_at: string
}

/** Der QR-Code fuer die iPhone-App: eine vorab bestaetigte Kopplung, die App holt ihren Schluessel selbst ab. */
export const deviceCodeApi = {
  /** 201. 422 `invalid_input`, 429 `pairing_limit`. */
  create: (address: string) => api.post<DeviceCode>('/pairings/device', { address }),
  /** 404 `pairing_not_found`, wenn der Code weg ist. */
  state: (id: string) => api.get<DeviceCodeState>(`/pairings/device/${encodeURIComponent(id)}`),
  withdraw: (id: string) => api.delete<void>(`/pairings/device/${encodeURIComponent(id)}`),
}
