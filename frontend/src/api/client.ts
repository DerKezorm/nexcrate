/**
 * Zugriff auf den nexcrate-Server.
 *
 * Jede Anfrage geht an `/api`, nimmt das Sitzungscookie mit (`same-origin`) und
 * schickt `X-Requested-With: nexcrate`. Ohne diesen Kopf weist der Server alles
 * ab, was etwas aendert (403 `csrf_header_missing`). Er steht auf jeder Anfrage,
 * nicht nur auf den aendernden: So vergisst ihn niemand beim naechsten Aufruf.
 *
 * Der Server uebersetzt nie. Ein Fehler kommt als
 * `{"detail": {"code": "...", "message": "...", ...Werte}}` mit `X-Request-Id`
 * im Kopf. `ApiError` haelt Code, Werte und Nummer fest, `errorText` baut daraus
 * den Satz aus `errors.json`.
 */

import type { TFunction } from 'i18next'

import { withBase } from '../lib/base'

/** `/api`, unter einem Unterpfad mit ihm davor (`lib/base.ts`). */
export const API_BASE = withBase('/api')

/**
 * Keine Antwort: Server aus, Netz weg, Proxy ohne Ziel. Der einzige Code, den
 * die Oberflaeche selbst vergibt; sein Text steht in errors.json wie die anderen.
 */
export const NETWORK_ERROR = 'server_unreachable'

/** Eine Antwort ohne verwertbaren Code, etwa eine HTML-Fehlerseite vom Proxy. */
export const UNKNOWN_ERROR = 'internal_error'

export class ApiError extends Error {
  /** 0, wenn gar keine Antwort kam. */
  readonly status: number
  readonly code: string
  /** Was neben `code` im Fehler stand, ohne `message`. Etwa `retry_after`. */
  readonly values: Readonly<Record<string, unknown>>
  readonly requestId: string | null

  constructor(status: number, code: string, values: Record<string, unknown> = {}, requestId: string | null = null) {
    super(`${code} (HTTP ${status})`)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.values = values
    this.requestId = requestId
  }
}

export type RequestOptions = {
  body?: unknown
  /**
   * Ein 401 ist hier eine Antwort und kein Verlust der Sitzung. Nur fuer den
   * Start, der mit `GET /auth/me` erst herausfindet, ob jemand angemeldet ist.
   */
  ignoreSessionLost?: boolean
  /** Bricht eine Anfrage ab, deren Antwort niemand mehr braucht. Der Abbruch kommt als AbortError, nicht als ApiError. */
  signal?: AbortSignal
}

/** Hier heisst 401 "falsches Passwort", nicht "Sitzung weg". */
const NOT_A_SESSION_LOSS = ['/auth/login']

let sessionLost: (() => void) | null = null

/** Der AuthProvider meldet sich hier an. Ein 401 `not_logged_in` fuehrt dann zur Anmeldeseite. */
export function setSessionLostHandler(handler: (() => void) | null): void {
  sessionLost = handler
}

export async function request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json', 'X-Requested-With': 'nexcrate' }
  let body: string | FormData | undefined
  if (options.body instanceof FormData) {
    // Eine Datei (Sicherung einspielen): den Kopf samt Grenze setzt der Browser selbst.
    body = options.body
  } else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(options.body)
  }

  let response: Response
  try {
    response = await fetch(API_BASE + path, { method, headers, body, credentials: 'same-origin', signal: options.signal })
  } catch (error) {
    // Selbst abgebrochen ist kein Ausfall des Servers.
    if (options.signal?.aborted) throw error
    throw new ApiError(0, NETWORK_ERROR)
  }

  const requestId = response.headers.get('X-Request-Id')
  if (!response.ok) {
    const error = await readError(response, requestId)
    const route = path.split('?')[0]
    if (error.status === 401 && error.code === 'not_logged_in' && !options.ignoreSessionLost && !NOT_A_SESSION_LOSS.includes(route)) {
      sessionLost?.()
    }
    throw error
  }
  if (response.status === 204) return undefined as T
  try {
    return (await response.json()) as T
  } catch {
    throw new ApiError(response.status, UNKNOWN_ERROR, {}, requestId)
  }
}

async function readError(response: Response, requestId: string | null): Promise<ApiError> {
  let detail: unknown = null
  try {
    const parsed: unknown = await response.json()
    if (parsed && typeof parsed === 'object') detail = (parsed as { detail?: unknown }).detail
  } catch {
    // Kein JSON, etwa eine Fehlerseite vom Proxy.
  }
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return new ApiError(response.status, UNKNOWN_ERROR, {}, requestId)

  const code = (detail as { code?: unknown }).code
  if (typeof code !== 'string' || code === '') return new ApiError(response.status, UNKNOWN_ERROR, {}, requestId)

  const values: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(detail)) {
    if (key !== 'code' && key !== 'message') values[key] = value
  }
  // Ein 500 traegt die Nummer auch im Koerper. Der Kopf gilt zuerst.
  const fromBody = typeof values.request_id === 'string' ? values.request_id : null
  return new ApiError(response.status, code, values, requestId ?? fromBody)
}

/**
 * Eine Datei vom Server holen und im Browser speichern (Sicherung herunterladen). POST, weil ein Passwort in den
 * Rumpf gehoert und nicht in eine Adresse, die im Verlauf und in jedem Protokoll landet.
 */
export async function downloadFile(path: string, body: unknown, fileName: string): Promise<void> {
  let response: Response
  try {
    response = await fetch(API_BASE + path, {
      method: 'POST',
      headers: { Accept: 'application/zip, application/json', 'Content-Type': 'application/json', 'X-Requested-With': 'nexcrate' },
      body: JSON.stringify(body),
      credentials: 'same-origin',
    })
  } catch {
    throw new ApiError(0, NETWORK_ERROR)
  }
  if (!response.ok) throw await readError(response, response.headers.get('X-Request-Id'))
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  document.body.append(link)
  link.click()
  link.remove()
  // Erst nach dem Klick freigeben, sonst bricht mancher Browser den Download ab.
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export const api = {
  get: <T>(path: string, options: Omit<RequestOptions, 'body'> = {}) => request<T>('GET', path, options),
  post: <T>(path: string, body?: unknown, options: Omit<RequestOptions, 'body'> = {}) => request<T>('POST', path, { ...options, body }),
  put: <T>(path: string, body?: unknown, options: Omit<RequestOptions, 'body'> = {}) => request<T>('PUT', path, { ...options, body }),
  patch: <T>(path: string, body?: unknown, options: Omit<RequestOptions, 'body'> = {}) => request<T>('PATCH', path, { ...options, body }),
  delete: <T>(path: string) => request<T>('DELETE', path),
}

/** Namen, die i18next als eigene Einstellung liest, statt sie in den Text einzusetzen. */
const I18NEXT_OPTIONS = new Set([
  'lng',
  'lngs',
  'ns',
  'context',
  'defaultValue',
  'replace',
  'returnObjects',
  'returnDetails',
  'joinArrays',
  'postProcess',
  'interpolation',
  'keyPrefix',
  'ordinal',
])

const MISSING = '\u0000nexcrate-missing-text'

/**
 * Der Satz zu einem Fehler in der eingestellten Sprache.
 *
 * Kennt `errors.json` den Code, steht dort der Text mit den Werten des Servers.
 * Kam gar keine Antwort, gilt `errors.server_unreachable`. Sonst, auch bei einer
 * Antwort ohne lesbaren Code, `errors.internal_error` mit der Nummer fuer die Fehlersuche.
 */
export function errorText(t: TFunction, error: unknown): string {
  const apiError = error instanceof ApiError ? error : null
  const request_id = apiError?.requestId ?? t('common.noRequestId')
  if (apiError && apiError.code !== UNKNOWN_ERROR) {
    const values: Record<string, string | number> = {}
    for (const [key, value] of Object.entries(apiError.values)) {
      if ((typeof value === 'string' || typeof value === 'number') && !I18NEXT_OPTIONS.has(key)) values[key] = value
    }
    const text = t(`errors.${apiError.code}`, { ...values, request_id, defaultValue: MISSING })
    if (text !== MISSING) return text
  }
  return t('errors.internal_error', { request_id })
}
