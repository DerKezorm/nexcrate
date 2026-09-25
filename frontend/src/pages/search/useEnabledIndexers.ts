import { useEffect, useState } from 'react'

import { indexersApi } from '../../api/indexers'

/**
 * Wie viele Indexer eingeschaltet sind, sobald `active` gilt. null, solange es niemand weiss: beim
 * Laden oder wenn die Liste nicht kam. Dann bleibt "Suchen" frei, und der Server sagt `no_indexers`.
 */
export function useEnabledIndexers(active: boolean): number | null {
  const [count, setCount] = useState<number | null>(null)

  useEffect(() => {
    if (!active) return
    let current = true
    indexersApi.list().then(
      (list) => {
        if (current) setCount(Array.isArray(list) ? list.filter((indexer) => indexer.enabled).length : null)
      },
      () => {
        if (current) setCount(null)
      },
    )
    return () => {
      current = false
    }
  }, [active])

  return count
}
