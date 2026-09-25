import { useCallback, useEffect, useState } from 'react'

import { blocklistApi } from '../../api/blocklist'
import type { BlocklistEntry } from '../../api/types'
import { isBlocklistEntry } from './blocklistText'

/**
 * Die gesperrten Releases eines Films, sobald `titleId` feststeht. Kommt die Liste nicht, zeigt die Seite nichts
 * dazu: Die Sperrliste ist eine Nebensache der Titelseite und soll dort keinen Fehlerkasten hinstellen.
 * `drop` nimmt einen Eintrag heraus, dessen Sperre aufgehoben ist.
 */
export function useBlocklist(titleId: number | null) {
  const [loaded, setLoaded] = useState<{ titleId: number | null; entries: BlocklistEntry[] }>({ titleId: null, entries: [] })

  useEffect(() => {
    if (titleId === null) return
    let current = true
    blocklistApi.list(titleId).then(
      (result) => {
        if (current) setLoaded({ titleId, entries: Array.isArray(result) ? result.filter(isBlocklistEntry) : [] })
      },
      () => {
        if (current) setLoaded({ titleId, entries: [] })
      },
    )
    return () => {
      current = false
    }
  }, [titleId])

  const drop = useCallback((id: number) => setLoaded((previous) => ({ ...previous, entries: previous.entries.filter((entry) => entry.id !== id) })), [])
  return { entries: loaded.titleId === titleId ? loaded.entries : [], drop }
}
