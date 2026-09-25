import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { renameApi, resultNumber, resultReasons, type RenameJob, type RenameUnit } from '../../api/rename'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage, Spinner } from '../../components/ui'
import { RenameSteps } from './RenameSteps'
import { skipText } from './renameText'
import { useRenameJob } from './useRenameJob'

/**
 * "Umbenennen" auf der Seite eines Films, einer Serie oder eines Albums (Antwort G1): das Fenster
 * zeigt "vorher, nachher" dieses einen Titels und benennt auf Knopfdruck um. Ein Album bleibt in seinem Kuenstlerordner.
 */
export function RenameTitleButton({ titleId, name, onRenamed }: { titleId: number; name: string; onRenamed?: () => void }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button variant="ghost" onClick={() => setOpen(true)} aria-label={t('settings.rename.titleButton.label', { name })}>
        <Symbol name="swap" />
        {t('settings.rename.titleButton.open')}
      </Button>
      {open && <RenameTitleDialog titleId={titleId} name={name} onClose={() => setOpen(false)} onRenamed={onRenamed} />}
    </>
  )
}

function RenameTitleDialog({ titleId, name, onClose, onRenamed }: { titleId: number; name: string; onClose: () => void; onRenamed?: () => void }) {
  const { t } = useTranslation()
  const [unit, setUnit] = useState<RenameUnit | null | undefined>(undefined)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [ended, setEnded] = useState<RenameJob | null>(null)
  const finished = useCallback(
    (job: RenameJob) => {
      if (job.action === 'run') {
        setEnded(job)
        onRenamed?.()
      }
    },
    [onRenamed],
  )
  const { job, error, starting, start, running } = useRenameJob(finished)

  useEffect(() => {
    let current = true
    renameApi.titlePreview(titleId).then(
      (found) => current && setUnit(found),
      (problem: unknown) => current && setLoadError(problem),
    )
    return () => {
      current = false
    }
  }, [titleId])

  const mine = running && job?.action === 'run'
  const ready = unit !== undefined && unit !== null && unit.skip === null
  const reasons = resultReasons(ended)

  return (
    <Dialog
      open
      wide
      title={t('settings.rename.titleButton.title', { name })}
      onClose={mine ? () => undefined : onClose}
      footer={
        ended !== null || !ready ? (
          <Button onClick={onClose}>{t('settings.rename.running.close')}</Button>
        ) : (
          <>
            <Button variant="ghost" onClick={onClose} disabled={mine}>
              {t('common.actions.cancel')}
            </Button>
            <Button onClick={() => void start(() => renameApi.renameTitle(titleId))} loading={starting || mine} disabled={running}>
              {t('settings.rename.titleButton.go')}
            </Button>
          </>
        )
      }
    >
      <div className="flex flex-col gap-3">
        {loadError !== null && <FormMessage>{errorText(t, loadError)}</FormMessage>}
        {error !== null && <FormMessage>{errorText(t, error)}</FormMessage>}
        {ended !== null ? (
          <>
            <FormMessage tone={resultNumber(ended, 'done') > 0 ? 'ok' : 'bad'} role="status">
              {resultNumber(ended, 'done') > 0 ? t('settings.rename.titleButton.done') : t('settings.rename.titleButton.notDone')}
            </FormMessage>
            {reasons.map(([code]) => (
              <p key={code} className="text-sm text-mist-400">
                {skipText(t, code)}
              </p>
            ))}
          </>
        ) : unit === undefined ? (
          loadError === null && (
            <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
              <Spinner />
              {t('common.loading')}
            </p>
          )
        ) : unit === null ? (
          <FormMessage tone="ok" role="status">
            {t('settings.rename.titleButton.nothing')}
          </FormMessage>
        ) : (
          <>
            <p className="text-sm text-mist-300">{unit.kind === 'music' ? t('settings.rename.titleButton.introAlbum') : t('settings.rename.titleButton.intro')}</p>
            <RenameSteps unit={unit} />
          </>
        )}
      </div>
    </Dialog>
  )
}
