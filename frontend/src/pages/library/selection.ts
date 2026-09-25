/**
 * Was gerade markiert ist (Rueckmeldung 20.09.2026). Der Modus wird angeschaltet, dann bekommt jede Zeile ein
 * Kaestchen. "Alle im Filter" markiert nicht 3.461 Nummern, sondern merkt sich die Ansicht: der Server bekommt dann
 * keine Liste, sondern Art, Zustand und Suchtext.
 */

import { useCallback, useMemo, useState } from 'react'

export type Selection = {
  /** Ob der Auswahlmodus an ist. */
  on: boolean
  /** Die markierten Nummern; leer, wenn die ganze Ansicht gemeint ist. */
  ids: ReadonlySet<number>
  /** Die ganze Ansicht ist gemeint, auch was noch nicht geladen ist. */
  whole: boolean
  start: () => void
  stop: () => void
  toggle: (id: number) => void
  page: (ids: number[]) => void
  all: () => void
  /** Wie viele markiert sind; bei der ganzen Ansicht die Zahl des Servers. */
  count: (total: number) => number
  /** Die Liste fuer den Server, oder null fuer die ganze Ansicht. */
  idsForServer: () => number[] | null
}

export function useSelection(): Selection {
  const [on, setOn] = useState(false)
  const [ids, setIds] = useState<ReadonlySet<number>>(() => new Set())
  const [whole, setWhole] = useState(false)

  const stop = useCallback(() => {
    setOn(false)
    setWhole(false)
    setIds(new Set())
  }, [])

  const toggle = useCallback((id: number) => {
    setWhole(false)
    setIds((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const page = useCallback((wanted: number[]) => {
    setWhole(false)
    setIds((current) => {
      // Sind schon alle dieser Seite markiert, nimmt der Klick sie wieder weg.
      const allThere = wanted.length > 0 && wanted.every((id) => current.has(id))
      const next = new Set(current)
      for (const id of wanted) {
        if (allThere) next.delete(id)
        else next.add(id)
      }
      return next
    })
  }, [])

  const all = useCallback(() => {
    setIds(new Set())
    setWhole(true)
  }, [])

  return useMemo(
    () => ({
      on,
      ids,
      whole,
      start: () => setOn(true),
      stop,
      toggle,
      page,
      all,
      count: (total: number) => (whole ? total : ids.size),
      idsForServer: () => (whole ? null : [...ids]),
    }),
    [on, ids, whole, stop, toggle, page, all],
  )
}
