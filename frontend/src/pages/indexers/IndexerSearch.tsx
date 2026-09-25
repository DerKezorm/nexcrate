import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { indexersApi } from '../../api/indexers'
import type { Indexer, IndexerKind, IndexerRelease, IndexerSearch as SearchAnswer } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Button, Field, FormMessage, Section, SelectField } from '../../components/ui'
import { formatAge, formatNumber } from '../../lib/format'
import { sizeText } from '../../lib/size'

type Shown = { answer: SearchAnswer; kind: IndexerKind }

/**
 * Eine Testsuche bei einem Indexer, um zu sehen, was er liefert. Eine Anfrage mit bis zu 50
 * Treffern, keine weiteren Seiten, nichts wird geladen. Kein Treffer ist ein Erfolg.
 */
export function IndexerSearch({ indexers }: { indexers: Indexer[] }) {
  const { t } = useTranslation()
  const [indexerId, setIndexerId] = useState<number | null>(null)
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [shown, setShown] = useState<Shown | null>(null)
  // Nur die neueste Suche zaehlt. Eine langsame alte ueberschreibt keine neuere.
  const generation = useRef(0)

  const chosen = indexers.find((indexer) => indexer.id === indexerId) ?? indexers.find((indexer) => indexer.enabled) ?? indexers[0]

  async function run() {
    const q = query.trim()
    if (q === '') return setProblem(t('indexers.search.missingQuery'))
    const current = ++generation.current
    const target = chosen
    setBusy(true)
    setProblem(null)
    try {
      const answer = await indexersApi.search(target.id, q)
      if (current !== generation.current) return
      setShown({ answer, kind: target.kind })
    } catch (error) {
      if (current !== generation.current) return
      setShown(null)
      setProblem(errorText(t, error))
    } finally {
      if (current === generation.current) setBusy(false)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void run()
  }

  return (
    <Section title={t('indexers.search.title')} intro={t('indexers.search.intro')}>
      <form onSubmit={submit} noValidate className="grid gap-3 sm:grid-cols-[minmax(0,14rem)_minmax(0,1fr)_auto] sm:items-end">
        <SelectField label={t('indexers.search.indexer')} value={String(chosen.id)} onChange={(event) => setIndexerId(Number(event.target.value))}>
          {indexers.map((indexer) => (
            <option key={indexer.id} value={indexer.id}>
              {indexer.name}
            </option>
          ))}
        </SelectField>
        <Field label={t('indexers.search.query')} type="search" value={query} onChange={(event) => setQuery(event.target.value)} maxLength={200} autoComplete="off" />
        <div>
          <Button type="submit" loading={busy}>
            {!busy && <Symbol name="search" />}
            {t('indexers.search.submit')}
          </Button>
        </div>
      </form>
      {problem && <FormMessage>{problem}</FormMessage>}
      {shown && <SearchResults answer={shown.answer} kind={shown.kind} />}
    </Section>
  )
}

function SearchResults({ answer, kind }: Shown) {
  const { t, i18n } = useTranslation()
  const language = i18n.language

  if (answer.results.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-ink-700 px-6 py-8 text-center text-sm text-mist-500" role="status">
        {t('indexers.search.none')}
      </p>
    )
  }

  const values = {
    total: formatNumber(answer.total, language),
    shown: formatNumber(answer.results.length, language),
    ms: formatNumber(answer.took_ms, language),
  }
  const summary =
    answer.results.length < answer.total ? t('indexers.search.summaryLimited', values) : t('indexers.search.summary', { ...values, count: answer.total })

  const size = (release: IndexerRelease) => (release.size_bytes !== null ? sizeText(t, release.size_bytes, language) : '')
  const age = (release: IndexerRelease) => (release.published_at !== null ? formatAge(release.published_at, language) : '')
  const count = (value: number | null) => (value === null ? '?' : formatNumber(value, language))
  const peers = (release: IndexerRelease) =>
    release.seeders === null && release.peers === null ? '' : t('indexers.search.peers', { seeders: count(release.seeders), peers: count(release.peers) })
  const grabs = (release: IndexerRelease) => (release.grabs === null ? '' : formatNumber(release.grabs, language))
  const shortLine = (release: IndexerRelease) =>
    [
      size(release),
      age(release),
      kind === 'torznab'
        ? peers(release) && t('indexers.search.peersShort', { seeders: count(release.seeders), peers: count(release.peers) })
        : release.grabs !== null && t('indexers.search.grabsShort', { grabs: grabs(release) }),
    ]
      .filter(Boolean)
      .join(' · ')

  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs text-mist-500" role="status">
        {summary}
      </p>
      <div className="overflow-x-auto rounded-xl border border-ink-700">
        <table className="w-full table-fixed text-sm" aria-label={t('indexers.search.resultsLabel')}>
          <thead className="bg-ink-900 text-left text-xs text-mist-500">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">
                {t('indexers.search.columnTitle')}
              </th>
              <th scope="col" className="hidden w-24 px-3 py-2 text-right font-medium sm:table-cell">
                {t('indexers.search.columnSize')}
              </th>
              <th scope="col" className="hidden w-36 px-3 py-2 font-medium sm:table-cell">
                {t('indexers.search.columnAge')}
              </th>
              <th scope="col" className="hidden w-32 px-3 py-2 text-right font-medium sm:table-cell">
                {kind === 'torznab' ? t('indexers.search.columnPeers') : t('indexers.search.columnGrabs')}
              </th>
            </tr>
          </thead>
          <tbody>
            {answer.results.map((release, index) => (
              <tr key={`${index}-${release.title}`} className="border-t border-ink-700/60 align-top">
                <td className="px-3 py-2">
                  <p className="font-mono text-xs leading-5 wrap-anywhere text-mist-200">{release.title}</p>
                  <p className="mt-0.5 text-xs text-mist-500 sm:hidden">{shortLine(release)}</p>
                </td>
                <td className="hidden px-3 py-2 text-right text-mist-300 tabular-nums sm:table-cell">{size(release)}</td>
                <td className="hidden px-3 py-2 text-mist-400 sm:table-cell">{age(release)}</td>
                <td className="hidden px-3 py-2 text-right text-mist-300 tabular-nums sm:table-cell">{kind === 'torznab' ? peers(release) : grabs(release)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
