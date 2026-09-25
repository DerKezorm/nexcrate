import type { ReactNode } from 'react'

import { BootLoading, UnreachablePage } from '../pages/BootPages'
import { LoginPage } from '../pages/LoginPage'
import { SetupPage } from '../pages/SetupPage'
import { useAuth } from './useAuth'

/**
 * Die App erst, wenn jemand angemeldet ist. Die Adresse bleibt dabei stehen:
 * Wer nach dem Anmelden weitermacht, landet dort, wo er war.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const { state } = useAuth()
  switch (state.phase) {
    case 'loading':
      return <BootLoading />
    case 'unreachable':
      return <UnreachablePage error={state.error} />
    case 'setup':
      return <SetupPage />
    case 'login':
      return <LoginPage notice={state.notice} />
    case 'ready':
      return children
  }
}
