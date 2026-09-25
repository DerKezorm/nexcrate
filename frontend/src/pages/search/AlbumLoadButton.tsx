import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError, errorText } from '../../api/client'
import { downloadsApi } from '../../api/downloads'
import type { AlbumSearchDecision, AlbumSearchRelease, Download, LoadConfirmation, SearchRelease } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage } from '../../components/ui'
import { albumRejectionText } from '../checker/musicCheckerText'
import { formatDateTime } from '../../lib/format'
import { DOWNLOADS_PATH } from '../downloads/address'
import { loadBlockLink, loadBlockText } from './loadText'

/** Was in dieser Albensuche geladen wurde: das Release, oder null. */
export type AlbumLoaded = { releaseKey: string; download: Download } | null

/**
 * "Laden" fuer ein Release dieses Albums (M4, Entscheidung 1): an der Karte "Würde nehmen" und an jeder Zeile. Wie bei
 * Filmen: gesperrt mit Grund und Weg, eine Rueckfrage bei einem Release, das nicht passt oder gesperrt ist, und nach
 * dem Laden ein Satz mit dem Weg zu den Downloads. Ein Release eines anderen Albums hat keinen Knopf.
 */
export function AlbumLoadButton({
  searchId,
  release,
  decision,
  loaded,
  onLoaded,
  prominent = false,
}: {
  searchId: string
  release: AlbumSearchRelease
  decision: AlbumSearchDecision
  loaded: AlbumLoaded
  onLoaded: (loaded: NonNullable<AlbumLoaded>) => void
  prominent?: boolean
}) {
  const { t, i18n } = useTranslation()
  const reasonId = useId()
  const [busy, setBusy] = useState(false)
  const [asking, setAsking] = useState<LoadConfirmation[] | null>(null)
  const [refused, setRefused] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)

  if (release.verdict === null || typeof release.can_load !== 'boolean' || decision.version_id === null) return null
  const versionId = decision.version_id
  const label = decision.label

  if (loaded !== null && loaded.releaseKey === release.release_key) {
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

  const block: string | null =
    loaded !== null ? 'download_active' : refused !== null ? refused : release.can_load ? null : (release.load_block ?? decision.load_block ?? '')
  const link = block !== null ? loadBlockLink(t, block) : null
  const spinning = busy && asking === null

  function blockText(code: string): string {
    if (code === 'version_fed_by_source') return t('search.album.load.fed')
    if (code === 'version_no_folder') return t('search.album.load.noFolder', { label })
    if (code === 'no_client_for_protocol') return release.protocol === 'torrent' ? t('search.load.block.noClientTorrent') : t('search.load.block.noClientUsenet')
    return loadBlockText(t, code, label, release as unknown as SearchRelease)
  }

  async function load(confirm: LoadConfirmation[]) {
    setBusy(true)
    setProblem(null)
    try {
      const download = await downloadsApi.load({ search_id: searchId, release_key: release.release_key, version_id: versionId, confirm })
      setAsking(null)
      onLoaded({ releaseKey: release.release_key, download })
    } catch (error) {
      const code = error instanceof ApiError ? error.code : null
      const missing: LoadConfirmation | null = code === 'release_not_fitting' ? 'not_fitting' : code === 'release_blocklisted' ? 'blocklisted' : null
      if (missing !== null && !confirm.includes(missing)) {
        setAsking([...confirm, missing])
      } else if (code !== null && ['version_fed_by_source', 'version_no_profile', 'version_no_folder', 'no_client_for_protocol', 'download_active'].includes(code)) {
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
    const needs: LoadConfirmation[] = []
    if (release.verdict !== null && !release.verdict.accepted) needs.push('not_fitting')
    if (release.blocklisted === true) needs.push('blocklisted')
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
            {blockText(block)}
          </span>
          {link && (
            <Link to={link.to} className="font-medium text-accent-400 hover:underline">
              {link.text}
            </Link>
          )}
        </p>
      )}
      {typeof release.would_wait_until === 'string' && release.can_load === true && (
        <p className="flex items-start gap-2 text-xs text-mist-400">
          <Symbol name="clock" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-mist-500" />
          <span className="min-w-0 wrap-anywhere">{t('search.load.wouldWait', { date: formatDateTime(release.would_wait_until, i18n.language) })}</span>
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
                <ul className="flex flex-col gap-1">
                  {(release.verdict?.rejections ?? []).map((rejection) => (
                    <li key={rejection.code} className="flex items-start gap-1.5 text-sm text-bad-500">
                      <Symbol name="alert" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      <span className="min-w-0 wrap-anywhere">{albumRejectionText(t, rejection, i18n.language)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {asking.includes('blocklisted') && <p className="text-sm text-mist-200">{t('search.load.confirm.blocklisted')}</p>}
            {problem !== null && <FormMessage>{problem}</FormMessage>}
          </div>
        </Dialog>
      )}
    </div>
  )
}
