import { useContext, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError, errorText } from '../../api/client'
import { downloadsApi, isLoadBlock } from '../../api/downloads'
import type { LoadConfirmation, SearchRelease, SearchReleaseVersion } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage } from '../../components/ui'
import { RejectionList } from '../checker/resultParts'
import { DOWNLOADS_PATH } from '../downloads/address'
import { formatDateTime, formatNumber } from '../../lib/format'
import { confirmationsFor, keepsOf, loadBlockLink, loadBlockText } from './loadText'
import { SearchLoadingContext } from './searchLoading'

/**
 * "Laden" fuer ein Release und eine Fassung, auf der Karte "Würde nehmen" und in den Gruenden einer
 * Zeile. Sendet `{search_id, release_key, version_id, confirm}` an `POST /api/downloads`.
 *
 * - Kann die Fassung nicht laden, ist der Knopf gesperrt, und darunter steht warum, mit dem Weg dorthin,
 *   wo es sich aendern laesst. Der Grund kommt aus der Suche (`load_block` am Release, sonst an der Fassung).
 * - Ein Release, das nicht passt oder gesperrt ist, laedt erst nach einer Rueckfrage. Sagt der Server
 *   `release_not_fitting` oder `release_blocklisted`, obwohl die Suche es nicht wusste, kommt die
 *   Rueckfrage ebenso. Ein Grund aus "Which versions can load" sperrt den Knopf mit diesem Grund.
 * - Nach dem Laden steht statt des Knopfes ein Satz mit dem Weg zu den Downloads. Jeder andere Knopf
 *   derselben Fassung sagt dann, dass schon ein Download laeuft.
 * - Ohne Suche drumherum oder bei einem Server, der `can_load` nicht schickt, gibt es keinen Knopf.
 */
export function LoadButton({ release, entry, prominent = false }: { release: SearchRelease; entry: SearchReleaseVersion; prominent?: boolean }) {
  const { t, i18n } = useTranslation()
  const search = useContext(SearchLoadingContext)
  const reasonId = useId()
  const [busy, setBusy] = useState(false)
  // Was die Rueckfrage bestaetigen laesst. null: keine Rueckfrage offen.
  const [asking, setAsking] = useState<LoadConfirmation[] | null>(null)
  // Ein Grund aus "Which versions can load", den der Server beim Laden genannt hat.
  const [refused, setRefused] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)

  if (search === null || typeof entry.can_load !== 'boolean') return null
  const loading = search
  const version = loading.versions.find((item) => item.version_id === entry.version_id)
  const label = version?.label ?? ''
  const loaded = loading.loaded.get(entry.version_id)
  // Eine Serienfassung laedt mehrere Releases (S4, Entscheidung 5): Ob Folgen schon laden, sagt der Server.
  const series = release.parsed_series !== undefined && release.parsed_series !== null
  const loadedHere = loading.loadedKeys?.has(`${entry.version_id}:${release.release_key}`) ?? loaded?.releaseKey === release.release_key

  if (loadedHere || (loaded !== undefined && loaded.releaseKey === release.release_key)) {
    return (
      <p role="status" className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-mist-200">
        <Symbol name="check" className="h-4 w-4 shrink-0 text-ok-500" />
        <span>{t('search.load.started')}</span>
        <Link to={DOWNLOADS_PATH} className="font-medium text-accent-400 hover:underline">
          {t('search.load.toDownloads')}
        </Link>
      </p>
    )
  }

  // '' heisst: gesperrt, aber ohne genannten Grund.
  const block: string | null =
    loaded !== undefined && !series ? 'download_active' : refused !== null ? refused : entry.can_load ? null : (entry.load_block ?? version?.load_block ?? '')
  const link = block !== null ? loadBlockLink(t, block) : null
  const spinning = busy && asking === null

  async function load(confirm: LoadConfirmation[]) {
    setBusy(true)
    setProblem(null)
    try {
      const download = await downloadsApi.load({ search_id: loading.searchId, release_key: release.release_key, version_id: entry.version_id, confirm })
      setAsking(null)
      loading.onLoaded(entry.version_id, release.release_key, download)
    } catch (error) {
      const code = error instanceof ApiError ? error.code : null
      const missing: LoadConfirmation | null =
        code === 'release_not_fitting' ? 'not_fitting' : code === 'release_blocklisted' ? 'blocklisted' : code === 'release_no_gain' ? 'no_gain' : null
      if (missing !== null && !confirm.includes(missing)) {
        // Der Server weiss etwas, das die Suche noch nicht wusste, etwa eine neue Sperre. Dann fragt die Seite.
        setAsking([...confirm, missing])
      } else if (isLoadBlock(code)) {
        setAsking(null)
        setRefused(code)
      } else if (code === 'search_expired') {
        setAsking(null)
        setProblem(t('search.problems.expired'))
      } else {
        setProblem(errorText(t, error))
      }
    } finally {
      setBusy(false)
    }
  }

  function start() {
    const needs = confirmationsFor(release, entry)
    setProblem(null)
    if (needs.length > 0) setAsking(needs)
    else void load([])
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div>
        <Button
          variant={prominent ? 'primary' : 'ghost'}
          size="sm"
          onClick={start}
          disabled={block !== null}
          loading={spinning}
          aria-label={t('search.load.label', { label, title: release.title })}
          aria-describedby={block !== null ? reasonId : undefined}
        >
          {!spinning && <Symbol name="download" />}
          {t('search.load.action')}
        </Button>
      </div>
      {block !== null && (
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-mist-400">
          <Symbol name="info" className="h-4 w-4 shrink-0 text-info-500" />
          <span id={reasonId} className="min-w-0 wrap-anywhere">
            {loadBlockText(t, block, label, release)}
          </span>
          {link && (
            <Link to={link.to} className="font-medium text-accent-400 hover:underline">
              {link.text}
            </Link>
          )}
        </p>
      )}
      {block === null && typeof entry.would_wait_until === 'string' && (
        <p className="flex items-start gap-2 text-xs text-mist-400">
          <Symbol name="clock" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-mist-500" />
          <span className="min-w-0 wrap-anywhere">{t('search.load.wouldWait', { date: formatDateTime(entry.would_wait_until, i18n.language) })}</span>
        </p>
      )}
      {problem !== null && asking === null && <FormMessage>{problem}</FormMessage>}
      {asking !== null && (
        <Dialog
          open
          title={t('search.load.confirm.title')}
          onClose={() => {
            if (busy) return
            setAsking(null)
            setProblem(null)
          }}
          footer={
            <>
              <Button
                variant="ghost"
                disabled={busy}
                onClick={() => {
                  setAsking(null)
                  setProblem(null)
                }}
              >
                {t('common.actions.cancel')}
              </Button>
              <Button variant="danger" loading={busy} onClick={() => void load(asking)}>
                {t('search.load.confirm.submit')}
              </Button>
            </>
          }
        >
          <div className="flex flex-col gap-3">
            <p className="font-mono text-sm leading-5 wrap-anywhere text-mist-100">{release.title}</p>
            {asking.includes('not_fitting') && (
              <div className="flex flex-col gap-2">
                <p className="text-sm text-mist-200">{t('search.load.confirm.notFitting', { label })}</p>
                {entry.result && <RejectionList rejections={entry.result.rejections} />}
                {entry.series_result && <RejectionList rejections={entry.series_result.rejections} />}
              </div>
            )}
            {asking.includes('blocklisted') && <p className="text-sm text-mist-200">{t('search.load.confirm.blocklisted')}</p>}
            {asking.includes('no_gain') && (
              <p className="text-sm text-mist-200">
                {t('search.load.confirm.noGain', { count: keepsOf(entry), value: formatNumber(keepsOf(entry), i18n.language) })}
              </p>
            )}
            {problem !== null && <FormMessage>{problem}</FormMessage>}
          </div>
        </Dialog>
      )}
    </div>
  )
}
