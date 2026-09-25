import { useEffect, useId, useState } from 'react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'

import { ApiError, errorText } from '../../api/client'
import { namingApi } from '../../api/naming'
import { sourcesApi } from '../../api/sources'
import type { NamingProblem, SourceNaming, VersionNaming } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button, FormMessage } from '../../components/ui'
import { namingNoteTexts } from './namingNotes'
import { Loading, NoteLines, PatternRows } from './PatternFields'

/** Der Einwand gegen ein Muster als Satz, wie ein Fehler des Servers. */
function problemText(t: TFunction, problem: NamingProblem | null | undefined): string | null {
  if (!problem || typeof problem.code !== 'string') return null
  // Der Einwand kam in der Antwort mit, nicht als Fehler: Status und Nummer gibt es dazu nicht.
  return errorText(t, new ApiError(422, problem.code, problem.values ?? {}))
}

/**
 * Radarrs Benennung, so wie nexcrate sie liest: beide Muster, darunter je Muster nexcrates erster Einwand und die
 * Hinweise, was Radarr anders macht (etwa kein Umbenennen beim Dateimuster). Genutzt im Dialog "Benennung aus Radarr"
 * und in der Pruefung der Uebernahme.
 */
export function SourceNamingView({ naming }: { naming: SourceNaming }) {
  const { t } = useTranslation()
  const notes = namingNoteTexts(t, naming.notes)
  return (
    <div className="flex flex-col gap-2.5">
      <PatternRows
        folder={naming.movie_folder ?? ''}
        file={naming.movie_file ?? ''}
        folderProblem={problemText(t, naming.problems?.movie_folder)}
        fileProblem={problemText(t, naming.problems?.movie_file)}
        folderNotes={notes.folder}
        fileNotes={notes.file}
      />
      <NoteLines notes={notes.general} />
    </div>
  )
}

/**
 * "Benennung aus Radarr übernehmen" fuer eine Fassung: liest Radarrs Benennung ueber die Verbindung, zeigt beide
 * Muster und uebernimmt sie nach der Bestaetigung mit `PUT /api/naming/versions/{id}`. Kann nexcrate ein Muster nicht
 * annehmen (`can_take` false), bleibt Uebernehmen gesperrt, und der Einwand steht beim Muster.
 */
export function RadarrNamingDialog({
  sourceId,
  versionId,
  label,
  onClose,
  onTaken,
}: {
  sourceId: number
  versionId: number
  label: string
  onClose: () => void
  onTaken: (saved: VersionNaming) => void
}) {
  const { t } = useTranslation()
  const reasonId = useId()
  const [naming, setNaming] = useState<SourceNaming | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const refused = naming !== null && !naming.can_take

  useEffect(() => {
    let current = true
    sourcesApi.naming(sourceId).then(
      (result) => {
        if (current) setNaming(result)
      },
      (error: unknown) => {
        if (current) setLoadError(error)
      },
    )
    return () => {
      current = false
    }
  }, [sourceId])

  async function take() {
    if (naming === null || !naming.can_take || busy) return
    setBusy(true)
    setProblem(null)
    try {
      onTaken(await namingApi.saveVersion(versionId, { movie_folder: naming.movie_folder, movie_file: naming.movie_file }))
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  function close() {
    if (!busy) onClose()
  }

  return (
    <Dialog
      open
      wide
      title={t('settings.files.radarrNaming.title', { label })}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void take()} loading={busy} disabled={naming === null || !naming.can_take} aria-describedby={refused ? reasonId : undefined}>
            {t('settings.files.radarrNaming.take')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-mist-300">{t('settings.files.radarrNaming.intro', { label })}</p>
        {loadError !== null ? (
          <FormMessage>{errorText(t, loadError)}</FormMessage>
        ) : naming === null ? (
          <Loading />
        ) : (
          <>
            <SourceNamingView naming={naming} />
            {refused && (
              <p id={reasonId} className="text-sm text-mist-300">
                {t('settings.files.radarrNaming.cannotTake', { label })}
              </p>
            )}
          </>
        )}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}
