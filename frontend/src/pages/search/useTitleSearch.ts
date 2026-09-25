import { useCallback, useEffect, useRef, useState } from 'react'

import { runningSearchId, SEARCH_POLL_MS, searchesApi } from '../../api/searches'
import type { Search, SearchStart, SearchStartBody } from '../../api/types'

/** Was schiefging: beim Starten oder beim Nachfragen. */
export type SearchProblem = { stage: 'start' | 'poll'; error: unknown }

/** Starten und Lesen einer Suche. Filme und Serien nehmen `searchesApi`, Alben seit M3 `albumSearchesApi`. */
export type SearchCalls<S, B> = {
  start: (titleId: string, body?: B) => Promise<SearchStart>
  get: (searchId: string, signal?: AbortSignal) => Promise<S>
}

type Run<S> = {
  titleId: string
  searchId: string | null
  search: S | null
  /** Vom Klick bis zur ersten Antwort mit Fortschritt. */
  starting: boolean
  problem: SearchProblem | null
}

export type TitleSearch<S = Search, B = SearchStartBody> = {
  search: S | null
  starting: boolean
  /** Die Suche startet oder laeuft. Solange ist der Knopf gesperrt. */
  busy: boolean
  problem: SearchProblem | null
  /** Ohne Umfang wie bei Filmen. Eine Serie sucht mit Umfang eine Staffel oder eine Folge (S3). */
  start: (scope?: B) => Promise<void>
}

function belongsTo<S>(run: Run<S> | null, titleId: string, searchId: string): run is Run<S> {
  return run !== null && run.titleId === titleId && run.searchId === searchId
}

/**
 * Die Suche fuer einen Titel: starten, einmal je Sekunde nachfragen, bis sie fertig ist.
 *
 * - 409 `search_running {search_id}` heisst, fuer den Titel laeuft schon eine. Die Seite folgt ihr.
 * - ⚠️ Verlaesst man die Seite oder wechselt den Titel, hoert das Nachfragen auf, die offene Anfrage
 *   wird abgebrochen, und keine spaete Antwort landet auf dem neuen Titel. Die Seite bleibt beim
 *   Wechsel von einem Titel zum naechsten dieselbe Komponente, deshalb reicht Aufraeumen beim
 *   Verlassen allein nicht.
 * - Scheitert das Nachfragen, bleibt der letzte Stand stehen und der Knopf wird wieder frei. Ein
 *   neuer Klick folgt ueber 409 der Suche, die am Server weiterlaeuft.
 */
export function useTitleSearch(titleId: string): TitleSearch
export function useTitleSearch<S extends { state: string }, B>(titleId: string, calls: SearchCalls<S, B>): TitleSearch<S, B>
export function useTitleSearch<S extends { state: string }, B>(titleId: string, calls?: SearchCalls<S, B>): TitleSearch<S, B> {
  const used = (calls ?? searchesApi) as unknown as SearchCalls<S, B>
  const [run, setRun] = useState<Run<S> | null>(null)
  // Zaehlt bei jedem Titelwechsel und beim Verlassen hoch. Eine Antwort auf einen alten Start zaehlt dann nicht mehr.
  const generation = useRef(0)

  useEffect(() => {
    generation.current += 1
    setRun(null)
    return () => {
      generation.current += 1
    }
  }, [titleId])

  const start = useCallback(async (scope?: B) => {
    const current = generation.current
    setRun({ titleId, searchId: null, search: null, starting: true, problem: null })
    let searchId: string
    try {
      searchId = String((await (scope === undefined ? used.start(titleId) : used.start(titleId, scope))).search_id)
    } catch (error) {
      if (current !== generation.current) return
      const running = runningSearchId(error)
      if (running === null) {
        setRun({ titleId, searchId: null, search: null, starting: false, problem: { stage: 'start', error } })
        return
      }
      searchId = running
    }
    if (current !== generation.current) return
    setRun({ titleId, searchId, search: null, starting: true, problem: null })
  }, [titleId, used])

  const pollTitle = run?.titleId ?? null
  const searchId = run?.searchId ?? null
  const stopped = run?.search?.state === 'done' || (run?.problem ?? null) !== null

  useEffect(() => {
    if (pollTitle === null || searchId === null || stopped) return
    const title = pollTitle
    const id = searchId
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const abort = new AbortController()

    async function poll() {
      try {
        const search = await used.get(id, abort.signal)
        if (!active) return
        setRun((current) => (belongsTo(current, title, id) ? { ...current, search, starting: false } : current))
        if (search.state !== 'done') timer = setTimeout(() => void poll(), SEARCH_POLL_MS)
      } catch (error) {
        if (!active) return
        setRun((current) => (belongsTo(current, title, id) ? { ...current, starting: false, problem: { stage: 'poll', error } } : current))
      }
    }

    void poll()
    return () => {
      active = false
      clearTimeout(timer)
      abort.abort()
    }
  }, [pollTitle, searchId, stopped, used])

  const current = run !== null && run.titleId === titleId ? run : null
  const running = current?.search !== null && current?.search !== undefined && current.search.state !== 'done'
  return {
    search: current?.search ?? null,
    starting: current?.starting ?? false,
    busy: current !== null && current.problem === null && (current.starting || running),
    problem: current?.problem ?? null,
    start,
  }
}
