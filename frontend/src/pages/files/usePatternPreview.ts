import { useEffect, useState } from 'react'

import { namingApi } from '../../api/naming'
import type { NamingPatterns, NamingPreview } from '../../api/types'

/** So lange wartet die Vorschau nach dem letzten Tastendruck, bevor sie den Server fragt. */
const PREVIEW_DELAY_MS = 300

export type PatternPreview = { result: NamingPreview | null; error: unknown }

/**
 * Die Vorschau zu Mustern, die beim Tippen mitlaeuft: erst nach einer kurzen Pause, und eine neuere bricht die
 * alte ab. `skip` fragt nichts an, etwa bei einem leeren Muster. Ob ein Muster gilt, sagt der Server.
 */
export function usePatternPreview(patterns: NamingPatterns | null, skip: boolean): PatternPreview {
  const [preview, setPreview] = useState<PatternPreview>({ result: null, error: null })

  useEffect(() => {
    if (patterns === null || skip) return
    const abort = new AbortController()
    const timer = setTimeout(() => {
      namingApi.preview(patterns, abort.signal).then(
        (result) => {
          if (!abort.signal.aborted) setPreview({ result, error: null })
        },
        (error: unknown) => {
          if (!abort.signal.aborted) setPreview({ result: null, error })
        },
      )
    }, PREVIEW_DELAY_MS)
    return () => {
      clearTimeout(timer)
      abort.abort()
    }
  }, [patterns, skip])

  return preview
}
