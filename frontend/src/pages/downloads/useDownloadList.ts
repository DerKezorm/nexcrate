import { useCallback, useEffect, useState } from 'react'

import { downloadClientsApi } from '../../api/downloadClients'
import { DOWNLOADS_POLL_MS, downloadsApi } from '../../api/downloads'
import { foreignApi } from '../../api/foreign'
import type { DownloadClient, DownloadList, DownloadsView, ForeignJob } from '../../api/types'

type Loaded = { key: string; list: DownloadList | null; clients: DownloadClient[] | null; foreign: ForeignJob[]; error: unknown }

/**
 * Eine Seite einer Ansicht der Downloads, dazu die Download-Programme (fuer "keines eingetragen" und
 * "nicht erreichbar"). Fragt alle 5 Sekunden nach, solange die Seite offen ist; die naechste Runde
 * beginnt erst, wenn die letzte fertig ist.
 *
 * - ⚠️ Wechselt die Ansicht oder die Seite, wird die offene Anfrage abgebrochen, und keine spaete
 *   Antwort landet in der neuen Ansicht.
 * - Scheitert eine Runde, bleibt der letzte Stand stehen, der Fehler steht dabei, und es geht weiter.
 * - `reload` fragt sofort, etwa nach dem Entfernen.
 * - Seit dem 22.09.2026 dazu die Auftraege in der Kategorie, die kein Download verfolgt. Scheitert das, bleibt der letzte
 *   Stand.
 */
export function useDownloadList(view: DownloadsView, page: number) {
  const key = `${view}|${page}`
  const [loaded, setLoaded] = useState<Loaded>({ key: '', list: null, clients: null, foreign: [], error: null })
  const [token, setToken] = useState(0)

  useEffect(() => {
    const current = `${view}|${page}`
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    let abort: AbortController | null = null

    async function round() {
      abort = new AbortController()
      const [list, clients, foreign] = await Promise.allSettled([downloadsApi.list(view, page, abort.signal), downloadClientsApi.list(), foreignApi.list(abort.signal)])
      if (!active) return
      setLoaded((previous) => {
        const same = previous.key === current
        return {
          key: current,
          list: list.status === 'fulfilled' ? list.value : same ? previous.list : null,
          clients: clients.status === 'fulfilled' ? clients.value : previous.clients,
          foreign: foreign.status === 'fulfilled' && Array.isArray(foreign.value?.items) ? foreign.value.items : previous.foreign,
          error: list.status === 'rejected' ? list.reason : null,
        }
      })
      timer = setTimeout(() => void round(), DOWNLOADS_POLL_MS)
    }

    void round()
    return () => {
      active = false
      clearTimeout(timer)
      abort?.abort()
    }
  }, [view, page, token])

  const reload = useCallback(() => setToken((count) => count + 1), [])
  const mine = loaded.key === key
  return { list: mine ? loaded.list : null, clients: loaded.clients, foreign: loaded.foreign, error: mine ? loaded.error : null, reload }
}
