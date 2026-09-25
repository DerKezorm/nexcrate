import { useCallback, useSyncExternalStore } from 'react'

function supported(): boolean {
  return typeof window.matchMedia === 'function'
}

/**
 * Ob eine Medienabfrage gerade zutrifft, neu gezeichnet, sobald sie kippt. Ohne `matchMedia` trifft keine zu:
 * jsdom hat keins, Tests stellen die Breite mit `test/viewport.ts` ein.
 */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (!supported()) return () => {}
      const list = window.matchMedia(query)
      list.addEventListener('change', onChange)
      return () => list.removeEventListener('change', onChange)
    },
    [query],
  )
  return useSyncExternalStore(subscribe, () => supported() && window.matchMedia(query).matches)
}
