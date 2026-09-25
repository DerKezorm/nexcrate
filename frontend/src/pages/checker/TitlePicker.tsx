import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { libraryApi } from '../../api/library'
import type { TitleSummary } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { FormMessage, Spinner } from '../../components/ui'

export type PickedTitle = { id: number; title: string; year: number | null }

/** Gesucht wird erst, wenn so lange nichts getippt wurde. */
export const TITLE_SEARCH_DELAY_MS = 300

/** Mehr Treffer braucht die Auswahl nicht; wer mehr sieht, tippt genauer. */
const TITLE_RESULTS = 6

type Kind = 'movie' | 'series'

/** Die Texte je Art, woertlich, damit `keys.test.ts` sie sieht. */
function useTexts(kind: Kind) {
  const { t } = useTranslation()
  if (kind === 'series') {
    return {
      label: t('checker.series.pick'),
      hint: t('checker.series.pickHint'),
      search: t('checker.series.pickSearch'),
      none: t('checker.series.pickNone'),
      clear: t('checker.series.pickClear'),
      searching: t('checker.movieSearching'),
    }
  }
  return {
    label: t('checker.movie'),
    hint: t('checker.movieHint'),
    search: t('checker.movieSearch'),
    none: t('checker.movieNone'),
    clear: t('checker.movieClear'),
    searching: t('checker.movieSearching'),
  }
}

/**
 * Ein Titel aus der eigenen Bibliothek, ueber dieselbe Suche wie die Bibliothek (sie kennt Umlaute).
 * Gewaehlt steht er als eine Zeile mit einem Knopf zum Entfernen da. Die Art entscheidet nur ueber
 * die Suche und die Texte.
 */
export function TitlePicker({ kind, value, onChange }: { kind: Kind; value: PickedTitle | null; onChange: (title: PickedTitle | null) => void }) {
  const { t } = useTranslation()
  const texts = useTexts(kind)
  const inputId = useId()
  const hintId = `${inputId}-hint`
  const [query, setQuery] = useState('')
  const [found, setFound] = useState<TitleSummary[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const q = query.trim()

  useEffect(() => {
    if (value !== null || q === '') {
      setFound(null)
      setSearching(false)
      setProblem(null)
      return
    }
    const abort = new AbortController()
    const timer = window.setTimeout(() => {
      setSearching(true)
      libraryApi.list({ kind, state: null, q, sort: 'title', page: 1 }, abort.signal).then(
        (page) => {
          if (abort.signal.aborted) return
          setFound(page.items.slice(0, TITLE_RESULTS))
          setSearching(false)
          setProblem(null)
        },
        (error: unknown) => {
          if (abort.signal.aborted) return
          setSearching(false)
          setProblem(error)
        },
      )
    }, TITLE_SEARCH_DELAY_MS)
    return () => {
      window.clearTimeout(timer)
      abort.abort()
    }
  }, [kind, q, value])

  if (value !== null) {
    return (
      <div className="flex min-w-0 flex-col gap-1.5">
        <span className="text-sm font-medium text-mist-300">{texts.label}</span>
        <div className="flex min-w-0 items-center gap-2 rounded-xl border border-accent-500/50 bg-accent-500/5 px-3 py-2">
          <Symbol name={kind === 'series' ? 'tv' : 'film'} className="h-4 w-4 shrink-0 text-accent-400" />
          <span className="min-w-0 flex-1 text-sm font-semibold wrap-anywhere text-mist-100">
            {value.title}
            {value.year !== null && value.year > 0 && <span className="ml-2 font-normal text-mist-500 tabular-nums">{value.year}</span>}
          </span>
          <button
            type="button"
            onClick={() => {
              onChange(null)
              setQuery('')
            }}
            aria-label={texts.clear}
            className="rounded-full p-1 text-mist-500 hover:bg-ink-800 hover:text-mist-100"
          >
            <Symbol name="close" className="h-4 w-4" />
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <label htmlFor={inputId} className="text-sm font-medium text-mist-300">
        {texts.label}
      </label>
      <div className="relative min-w-0">
        <Symbol name="search" className="pointer-events-none absolute top-1/2 left-3.5 h-4 w-4 -translate-y-1/2 text-mist-600" />
        <input
          id={inputId}
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={texts.search}
          aria-describedby={hintId}
          maxLength={200}
          autoComplete="off"
          className="w-full rounded-xl border border-ink-700 bg-ink-900 py-2.5 pr-4 pl-10 text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
        />
      </div>
      <p id={hintId} className="text-xs text-mist-500">
        {texts.hint}
      </p>
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      {q !== '' &&
        (found === null ? (
          searching && (
            <p className="flex items-center gap-2 text-xs text-mist-500" role="status">
              <Spinner className="h-3.5 w-3.5" />
              {texts.searching}
            </p>
          )
        ) : found.length === 0 ? (
          <p className="text-xs text-mist-500" role="status">
            {texts.none}
          </p>
        ) : (
          <ul className="flex flex-col gap-1">
            {found.map((title) => (
              <li key={title.id}>
                <button
                  type="button"
                  onClick={() => onChange({ id: title.id, title: title.title, year: title.year })}
                  className="flex w-full min-w-0 items-baseline gap-2 rounded-lg border border-ink-700 bg-ink-900/60 px-3 py-2 text-left text-sm transition-colors hover:border-accent-500/50 hover:bg-ink-800"
                >
                  <span className="min-w-0 flex-1 wrap-anywhere text-mist-100">{title.title}</span>
                  {title.year !== null && title.year > 0 && <span className="shrink-0 text-xs text-mist-500 tabular-nums">{title.year}</span>}
                </button>
              </li>
            ))}
          </ul>
        ))}
    </div>
  )
}
