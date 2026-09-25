import { useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { errorText } from '../../api/client'
import { downloadsApi } from '../../api/downloads'
import type { Download, ForeignJob } from '../../api/types'
import { buttonClasses } from '../../components/buttonClasses'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, ProgressBar } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { percentOf } from '../../lib/states'
import { CLIENTS_TAB_PATH } from '../settings/tabs'
import { AlbumAssignDialog } from './AlbumAssignDialog'
import { AssignDialog } from './AssignDialog'
import { ChooseDialog } from './ChooseDialog'
import { DownloadHead } from './DownloadHead'
import { FinishDialog } from './FinishDialog'
import { ForeignJobs } from './ForeignJobs'
import { problemActions } from './problemActions'
import { clientNameOf, isRetryable, problemReasonText, problemWhyText, proposalOf } from './downloadText'
import { MappingDialog, PathPair } from './MappingDialog'
import { RemoveDownloadDialog } from './RemoveDownloadDialog'

/**
 * Zwei Gruppen: was den Besitzer braucht, und Hinweise, um die sich nexcrate selbst kuemmert. Seit dem 22.09.2026 dazu
 * die Auftraege in der Kategorie, die nexcrate nicht bestellt hat.
 */
export function ProblemList({ items, foreign = [], onChanged }: { items: Download[]; foreign?: ForeignJob[]; onChanged: (message?: string) => void }) {
  const { t } = useTranslation()
  const needs = items.filter((item) => item.problem?.needs_owner === true)
  const hints = items.filter((item) => item.problem?.needs_owner !== true)

  if (items.length === 0 && foreign.length === 0) {
    return <p className="rounded-2xl border border-dashed border-ink-700 px-6 py-10 text-center text-sm text-mist-500">{t('downloads.problems.empty')}</p>
  }

  return (
    <div className="flex flex-col gap-10">
      {needs.length > 0 && (
        <Group urgent title={t('downloads.problems.needsYou.title')} intro={t('downloads.problems.needsYou.intro')} items={needs} onChanged={onChanged} />
      )}
      <ForeignJobs jobs={foreign} onChanged={onChanged} />
      {hints.length > 0 && (
        <Group urgent={false} title={t('downloads.problems.hints.title')} intro={t('downloads.problems.hints.intro')} items={hints} onChanged={onChanged} />
      )}
    </div>
  )
}

function Group({ urgent, title, intro, items, onChanged }: { urgent: boolean; title: string; intro: string; items: Download[]; onChanged: (message?: string) => void }) {
  const { i18n } = useTranslation()
  return (
    <section aria-label={title} className="flex flex-col gap-4">
      <div>
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          <Symbol name={urgent ? 'alert' : 'info'} className={'h-5 w-5 ' + (urgent ? 'text-bad-500' : 'text-info-500')} />
          {title}
          <Badge tone={urgent ? 'bad' : 'info'}>{formatNumber(items.length, i18n.language)}</Badge>
        </h2>
        <p className="mt-1 max-w-3xl text-sm text-mist-500">{intro}</p>
      </div>
      <ul className="flex flex-col gap-4">
        {items.map((item) => (
          <li key={item.id} className="min-w-0">
            <ProblemCard download={item} urgent={urgent} onChanged={onChanged} />
          </li>
        ))}
      </ul>
    </section>
  )
}

type OpenDialog = 'mapping' | 'remove' | 'removeAndBlock' | 'assign' | 'assignAlbum' | 'choose' | 'finish' | null


/**
 * Ein Problem: was passiert ist, warum, und der naechste Schritt als Knopf. Welche Knoepfe es gibt,
 * haengt am Code: Zuordnung uebernehmen, erneut versuchen, entfernen (und sperren), zum Film, wo man
 * neu sucht, oder zu den Download-Programmen.
 */
function ProblemCard({ download, urgent, onChanged }: { download: Download; urgent: boolean; onChanged: (message?: string) => void }) {
  const { t } = useTranslation()
  const [dialog, setDialog] = useState<OpenDialog>(null)
  const [retrying, setRetrying] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const info = download.problem ?? { code: '', needs_owner: urgent, values: {} }
  const code = info.code
  const client = clientNameOf(t, download)
  const proposal = proposalOf(info)
  const title = download.title.title
  const percent = download.state === 'downloading' && download.progress !== null ? percentOf(download.progress) : null

  async function clear() {
    setClearing(true)
    setProblem(null)
    try {
      await downloadsApi.clear(download.id)
      onChanged(t('downloads.problems.cleared'))
    } catch (error) {
      setProblem(error)
    } finally {
      setClearing(false)
    }
  }

  async function retry() {
    setRetrying(true)
    setProblem(null)
    try {
      await downloadsApi.retry(download.id)
      onChanged(t('downloads.problems.retried'))
    } catch (error) {
      setProblem(error)
    } finally {
      setRetrying(false)
    }
  }

  const retryButton = isRetryable(download.state) ? (
    <Button size="sm" onClick={() => void retry()} loading={retrying} aria-label={t('downloads.actions.retryLabel', { title })}>
      {!retrying && <Symbol name="refresh" />}
      {t('downloads.actions.retry')}
    </Button>
  ) : null
  const removeButton = (
    <Button variant="ghost" size="sm" onClick={() => setDialog('remove')} aria-label={t('downloads.actions.removeLabel', { title })}>
      <Symbol name="trash" />
      {t('downloads.actions.remove')}
    </Button>
  )
  const removeAndBlockButton = (
    <Button variant="danger" size="sm" onClick={() => setDialog('removeAndBlock')} aria-label={t('downloads.actions.removeAndBlockLabel', { title })}>
      <Symbol name="trash" />
      {t('downloads.actions.removeAndBlock')}
    </Button>
  )
  const toTitle = (
    <Link to={`/titel/${download.title.id}`} className={buttonClasses('ghost', 'sm')} aria-label={t('downloads.actions.toTitleLabel', { title })}>
      <Symbol name="search" />
      {t('downloads.actions.toTitle')}
    </Link>
  )

  const buttons: Record<string, ReactNode> = {
    retry: retryButton,
    clear: (
      <Button variant="ghost" size="sm" onClick={() => void clear()} loading={clearing} aria-label={t('downloads.actions.clearLabel', { title })}>
        {!clearing && <Symbol name="check" />}
        {t('downloads.actions.clear')}
      </Button>
    ),
    remove: removeButton,
    removeAndBlock: removeAndBlockButton,
    toTitle,
    mapping: (
      <Button size="sm" onClick={() => setDialog('mapping')} aria-label={t('downloads.actions.mappingLabel', { title })}>
        <Symbol name="link" />
        {t('downloads.actions.mapping')}
      </Button>
    ),
    toClients: (
      <Link to={CLIENTS_TAB_PATH} className={buttonClasses('ghost', 'sm')}>
        <Symbol name="server" />
        {t('downloads.actions.toClients')}
      </Link>
    ),
    assign: (
      <Button size="sm" onClick={() => setDialog('assign')} aria-label={t('downloads.actions.assignLabel', { title })}>
        <Symbol name="link" />
        {t('downloads.actions.assign')}
      </Button>
    ),
    assignAlbum: (
      <Button size="sm" onClick={() => setDialog('assignAlbum')} aria-label={t('downloads.actions.assignLabel', { title })}>
        <Symbol name="link" />
        {t('downloads.actions.assign')}
      </Button>
    ),
    finish: (
      <Button variant="ghost" size="sm" onClick={() => setDialog('finish')} aria-label={t('downloads.actions.finishLabel', { title })}>
        {t('downloads.actions.finish')}
      </Button>
    ),
    choose: (
      <Button size="sm" onClick={() => setDialog('choose')} aria-label={t('downloads.actions.chooseLabel', { title })}>
        <Symbol name="check" />
        {t('downloads.actions.choose')}
      </Button>
    ),
  }
  const album = download.scope?.kind === 'album'
  const actions = problemActions(code, download.state, proposal !== null, download.scope != null && !album, album).map((name) => (
    <span key={name} className="contents">
      {buttons[name]}
    </span>
  ))

  return (
    <article
      aria-label={title}
      className={
        'flex min-w-0 flex-col gap-3 rounded-2xl border border-l-4 p-4 sm:p-5 ' +
        (urgent ? 'border-bad-500/40 border-l-bad-500 bg-bad-500/5' : 'border-ink-700 border-l-info-500/60 bg-ink-850/80')
      }
    >
      <DownloadHead download={download} hint={!urgent} />
      <div className="flex flex-col gap-1">
        <p className="text-base font-medium wrap-anywhere text-mist-100">{problemReasonText(t, code, client)}</p>
        <p className="text-sm wrap-anywhere text-mist-400">{problemWhyText(t, info, client, album)}</p>
      </div>
      {proposal && (
        <div className="flex flex-col gap-2 rounded-xl border border-accent-500/30 bg-accent-500/5 p-3">
          <p className="flex items-center gap-1.5 text-xs font-semibold text-accent-400">
            <Symbol name="sparkle" className="h-3.5 w-3.5" />
            {t('downloads.problems.proposal')}
          </p>
          <PathPair mapping={proposal} />
        </div>
      )}
      {percent !== null && <ProgressBar value={percent / 100} tone="accent" label={t('downloads.active.progress', { title })} />}
      <p className="font-mono text-xs leading-5 wrap-anywhere text-mist-600">{download.release.title}</p>
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      <div className="flex flex-wrap gap-2">{actions}</div>

      {dialog === 'mapping' && proposal && (
        <MappingDialog
          download={download}
          mapping={proposal}
          onClose={() => setDialog(null)}
          onDone={() => {
            setDialog(null)
            onChanged(t('downloads.mapping.done'))
          }}
        />
      )}
      {dialog === 'assign' && (
        <AssignDialog
          download={download}
          onClose={() => setDialog(null)}
          onDone={(message) => {
            setDialog(null)
            onChanged(message)
          }}
        />
      )}
      {dialog === 'assignAlbum' && (
        <AlbumAssignDialog
          download={download}
          onClose={() => setDialog(null)}
          onDone={(message) => {
            setDialog(null)
            onChanged(message)
          }}
        />
      )}
      {dialog === 'finish' && (
        <FinishDialog
          download={download}
          onClose={() => setDialog(null)}
          onDone={(message) => {
            setDialog(null)
            onChanged(message)
          }}
        />
      )}
      {dialog === 'choose' && (
        <ChooseDialog
          download={download}
          onClose={() => setDialog(null)}
          onDone={(message) => {
            setDialog(null)
            onChanged(message)
          }}
        />
      )}
      {(dialog === 'remove' || dialog === 'removeAndBlock') && (
        <RemoveDownloadDialog
          download={download}
          blocklist={dialog === 'removeAndBlock'}
          onClose={() => setDialog(null)}
          onRemoved={() => {
            setDialog(null)
            onChanged(t('downloads.remove.done'))
          }}
        />
      )}
    </article>
  )
}
