import { useContext } from 'react'

import { NoticeContext } from './NoticeContext'

/**
 * Rueckmeldung ohne eigenen Platz auf der Seite: `const notify = useNotice()`, dann
 * `notify(text)`. Etwa fuer Knoepfe, die erst ein spaeterer Schritt kann. Ein Knopf
 * ohne jede Rueckmeldung sieht kaputt aus.
 */
export function useNotice(): (text: string) => void {
  return useContext(NoticeContext)
}
