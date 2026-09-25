import { useEffect, useState } from 'react'

import { tagsApi } from '../api/tags'

/**
 * Die Namen aller Tags, als Vorschlaege beim Tippen. Eine Antwort in anderer Form bringt keine. `enabled` false fragt
 * noch nicht: die Titelseite holt die Liste erst, wenn jemand ins Feld klickt.
 */
export function useTagNames(enabled = true): string[] {
  const [names, setNames] = useState<string[]>([])
  useEffect(() => {
    if (!enabled) return
    let current = true
    tagsApi.list().then(
      (found) => current && Array.isArray(found?.items) && setNames(found.items.map((item) => item.label)),
      () => undefined,
    )
    return () => {
      current = false
    }
  }, [enabled])
  return names
}
