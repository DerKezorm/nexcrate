import { createContext } from 'react'

import type { Me } from '../api/types'
import type { Language } from '../i18n/languages'

/** Warum die Anmeldeseite erscheint, wenn nicht einfach beim Start. */
export type LoginNotice = 'sessionEnded' | 'setupClosed'

export type AuthState =
  | { phase: 'loading' }
  | { phase: 'unreachable'; error: unknown }
  | { phase: 'setup' }
  | { phase: 'login'; notice: LoginNotice | null }
  | { phase: 'ready'; me: Me }

export type AuthValue = {
  state: AuthState
  /** Das Konto, sobald jemand angemeldet ist. */
  me: Me | null
  /** Startet neu, ohne den aktuellen Bildschirm vorher wegzunehmen. */
  retry: () => Promise<void>
  login: (username: string, password: string) => Promise<void>
  setup: (username: string, password: string, language: Language) => Promise<void>
  logout: () => Promise<void>
  logoutAll: () => Promise<void>
  /** Wechselt die Sprache sofort. Angemeldet merkt sich der Server sie fuers Konto. */
  setLanguage: (language: Language) => Promise<void>
}

export const AuthContext = createContext<AuthValue | null>(null)
