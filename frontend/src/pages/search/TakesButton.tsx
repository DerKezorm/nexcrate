import { useContext, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError, errorText } from '../../api/client'
import { downloadsApi } from '../../api/downloads'
import type { SearchRelease, SearchVersion, TakesResult } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { sizeText } from '../../lib/size'
import { DOWNLOADS_PATH } from '../downloads/address'
import { SearchLoadingContext } from './searchLoading'

/**
 * "Alles laden" unter "Würde nehmen" einer Serienfassung (S4, Entscheidung 3): vorher ein Dialog mit Zahl, Groesse und
 * was es fuellt und ersetzt, danach je Release das Ergebnis. Scheitert eines, laufen die anderen weiter.
 */
export function TakesButton({ version, releases }: { version: SearchVersion; releases: readonly SearchRelease[] }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const search = useContext(SearchLoadingContext)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [results, setResults] = useState<TakesResult['results'] | null>(null)
  const [problem, setProblem] = useState<unknown>(null)
  const takes = version.takes ?? []
  if (search === null || takes.length === 0 || typeof version.takes_can_load !== 'boolean') return null
  const loading = search

  const size = takes.reduce((sum, take) => sum + (releases.find((release) => release.release_key === take.release_key)?.size_bytes ?? 0), 0)
  const fills = takes.reduce((sum, take) => sum + take.fills.length, 0)
  const replaces = takes.reduce((sum, take) => sum + take.replaces.length, 0)

  async function load() {
    setBusy(true)
    setProblem(null)
    try {
      const answer = await downloadsApi.takes({ search_id: loading.searchId, version_id: version.version_id, confirm: [] })
      setResults(answer.results)
      for (const item of answer.results) {
        if (item.download) loading.onLoaded(version.version_id, item.release_key, item.download)
      }
    } catch (error) {
      setProblem(error instanceof ApiError && error.code === 'search_expired' ? t('search.problems.expired') : errorText(t, error))
    } finally {
      setBusy(false)
    }
  }

  function close() {
    if (busy) return
    setOpen(false)
    setResults(null)
    setProblem(null)
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div>
        <Button size="sm" onClick={() => setOpen(true)} disabled={!version.takes_can_load} aria-label={t('search.series.takes.label', { label: version.label })}>
          <Symbol name="download" />
          {t('search.series.takes.action')}
        </Button>
      </div>
      {!version.takes_can_load && <p className="text-xs text-mist-400">{t('search.series.takes.blocked')}</p>}
      {open && (
        <Dialog
          open
          wide
          title={t('search.series.takes.title', { label: version.label })}
          onClose={close}
          footer={
            results === null ? (
              <>
                <Button variant="ghost" onClick={close} disabled={busy}>
                  {t('common.actions.cancel')}
                </Button>
                <Button onClick={() => void load()} loading={busy}>
                  {t('search.series.takes.submit')}
                </Button>
              </>
            ) : (
              <Button onClick={close}>{t('common.actions.close')}</Button>
            )
          }
        >
          <div className="flex flex-col gap-3">
            <p className="text-sm text-mist-200 tabular-nums">
              {t('search.series.takes.summaryParts', {
                releases: t('search.series.takes.releasesPart', { count: takes.length, value: formatNumber(takes.length, language) }),
                size: sizeText(t, size, language),
                // Nullen fallen weg; die Mehrzahl haengt an der jeweiligen Zahl.
                parts: [
                  fills > 0 ? t('search.series.takes.fillsPart', { count: fills, value: formatNumber(fills, language) }) : null,
                  replaces > 0 ? t('search.series.takes.replacesPart', { count: replaces, value: formatNumber(replaces, language) }) : null,
                ]
                  .filter((part): part is string => part !== null)
                  .join(', '),
              })}
            </p>
            <ul className="flex flex-col gap-2">
              {takes.map((take) => {
                const release = releases.find((item) => item.release_key === take.release_key)
                const result = results?.find((item) => item.release_key === take.release_key)
                return (
                  <li key={take.release_key} className="flex min-w-0 flex-col gap-1 rounded-lg border border-ink-700 bg-ink-900/60 px-3 py-2">
                    <p className="font-mono text-xs leading-5 wrap-anywhere text-mist-200">{release?.title ?? take.release_key}</p>
                    {result?.download && (
                      <p className="flex items-center gap-1.5 text-xs text-ok-400">
                        <Symbol name="check" className="h-3.5 w-3.5" />
                        {t('search.series.takes.loaded')}
                      </p>
                    )}
                    {result?.error && <FormMessage>{errorText(t, new ApiError(409, result.error.code, result.error.values, ''))}</FormMessage>}
                  </li>
                )
              })}
            </ul>
            {results !== null && (
              <p className="flex flex-wrap items-center gap-x-2 text-sm text-mist-200">
                <span>{t('search.series.takes.done')}</span>
                <Link to={DOWNLOADS_PATH} className="font-medium text-accent-400 hover:underline">
                  {t('search.load.toDownloads')}
                </Link>
              </p>
            )}
            {problem !== null && <FormMessage>{typeof problem === 'string' ? problem : errorText(t, problem)}</FormMessage>}
          </div>
        </Dialog>
      )}
    </div>
  )
}
