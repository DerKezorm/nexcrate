import { useContext } from 'react'

import { AuthContext, type AuthValue } from './AuthContext'

export function useAuth(): AuthValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth needs an AuthProvider')
  return value
}
