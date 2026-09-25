import { useEffect, useState } from 'react'

import { musicApi } from '../../api/music'
import type { LoadingStatus } from '../../api/types'

/** So oft fragt die Musik-Bibliothek den Ladezustand nach (M1.2). */
export const LOADING_POLL_MS = 4000

/**
 * Der Ladezustand aus `GET /api/music/loading` (M1.2), neu gefragt, solange etwas laeuft oder wartet. Scheitert die
 * Anfrage einmal, bleibt der letzte Stand stehen; es gibt keine eigene Fehlermeldung dafuer, die Zeile ist nur ein
 * Hinweis.
 */
export function useLoadingStatus(): LoadingStatus | null {
  const [status, setStatus] = useState<LoadingStatus | null>(null)

  useEffect(() => {
    let current = true
    let timer: number | null = null

    function ask() {
      musicApi.loading().then(
        (result) => {
          if (!current) return
          setStatus(result)
          timer = window.setTimeout(ask, LOADING_POLL_MS)
        },
        () => {
          if (current) timer = window.setTimeout(ask, LOADING_POLL_MS)
        },
      )
    }
    ask()

    return () => {
      current = false
      if (timer !== null) window.clearTimeout(timer)
    }
  }, [])

  return status
}
