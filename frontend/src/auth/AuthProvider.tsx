import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { authApi } from '../api/auth'
import { ApiError, setSessionLostHandler } from '../api/client'
import type { Me } from '../api/types'
import { changeLanguage } from '../i18n'
import { isLanguage, type Language } from '../i18n/languages'
import { AuthContext, type AuthState, type AuthValue } from './AuthContext'

/**
 * Wer angemeldet ist, und ob es das Konto schon gibt.
 *
 * Start: erst `GET /setup/status`. Braucht es die Einrichtung, kommt die
 * Einrichtungsseite. Sonst `GET /auth/me`: ein Konto heisst App, 401 heisst
 * Anmeldeseite. Alles andere (keine Antwort, Serverfehler) zeigt einen Bildschirm
 * mit "Erneut versuchen". Auf der Anmeldeseite wuerde dann jeder Versuch scheitern,
 * und niemand sieht, warum.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ phase: 'loading' })
  // Jeder Start und jeder Wechsel zaehlt hoch. Die spaete Antwort eines alten
  // Starts (StrictMode startet in der Entwicklung doppelt) ueberschreibt so nichts.
  const generation = useRef(0)
  const stateRef = useRef(state)
  stateRef.current = state

  const enter = useCallback(async (me: Me, run: number) => {
    if (isLanguage(me.language)) await changeLanguage(me.language)
    if (run === generation.current) setState({ phase: 'ready', me })
  }, [])

  const boot = useCallback(
    async (keepScreen: boolean) => {
      const run = ++generation.current
      if (!keepScreen) setState({ phase: 'loading' })
      try {
        const status = await authApi.setupStatus()
        if (run !== generation.current) return
        if (status.setup_required) {
          setState({ phase: 'setup' })
          return
        }
        const me = await authApi.me()
        if (run !== generation.current) return
        await enter(me, run)
      } catch (error) {
        if (run !== generation.current) return
        if (error instanceof ApiError && error.status === 401) setState({ phase: 'login', notice: null })
        else setState({ phase: 'unreachable', error })
      }
    },
    [enter],
  )

  useEffect(() => {
    setSessionLostHandler(() => {
      // Nur aus der App heraus. Wer gerade abmeldet oder schon auf der
      // Anmeldeseite steht, soll keinen Hinweis auf eine abgelaufene Sitzung sehen.
      if (stateRef.current.phase !== 'ready') return
      generation.current++
      setState({ phase: 'login', notice: 'sessionEnded' })
    })
    void boot(false)
    return () => setSessionLostHandler(null)
  }, [boot])

  const retry = useCallback(() => boot(true), [boot])

  const login = useCallback(
    async (username: string, password: string) => {
      const run = ++generation.current
      const me = await authApi.login(username, password)
      await enter(me, run)
    },
    [enter],
  )

  const setup = useCallback(
    async (username: string, password: string, language: Language) => {
      const run = ++generation.current
      try {
        const me = await authApi.setup({ username, password, language })
        await enter(me, run)
      } catch (error) {
        // Das Konto gibt es schon: Jemand war schneller, oder die Seite war alt.
        if (error instanceof ApiError && error.status === 404 && error.code === 'not_found') {
          if (run === generation.current) setState({ phase: 'login', notice: 'setupClosed' })
          return
        }
        throw error
      }
    },
    [enter],
  )

  const leave = useCallback(async (call: () => Promise<void>) => {
    try {
      await call()
    } catch (error) {
      // 401 heisst: schon abgemeldet. Das Ziel ist erreicht.
      if (!(error instanceof ApiError && error.status === 401)) throw error
    }
    generation.current++
    setState({ phase: 'login', notice: null })
  }, [])

  const logout = useCallback(() => leave(authApi.logout), [leave])
  const logoutAll = useCallback(() => leave(authApi.logoutAll), [leave])

  const setLanguage = useCallback(async (language: Language) => {
    await changeLanguage(language)
    const current = stateRef.current
    if (current.phase !== 'ready' || current.me.language === language) return
    const me = await authApi.setLanguage(language)
    setState((previous) => (previous.phase === 'ready' ? { phase: 'ready', me } : previous))
  }, [])

  const value = useMemo<AuthValue>(
    () => ({
      state,
      me: state.phase === 'ready' ? state.me : null,
      retry,
      login,
      setup,
      logout,
      logoutAll,
      setLanguage,
    }),
    [state, retry, login, setup, logout, logoutAll, setLanguage],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
