import { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import type { Source, TakeoverJob } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage, ProgressBar, Spinner } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { companionCountRows as companionRows, companionCountText } from '../files/companionText'
import { FolderPicker } from '../files/FolderPicker'
import { MusicTakeoverCheck } from './MusicTakeoverCheck'
import { TakeoverCheck, TakeoverFigures, WarningNote } from './TakeoverCheck'
import {
  blockText,
  chosenMappings,
  DEFAULT_CHOICES,
  jobErrorText,
  jobReasonText,
  phaseText,
  sameChosen,
  takeoverBlock,
  takeoverProgress,
  takeoverKind,
  takeoverRequest,
  type ChosenFolders,
  type TakeoverChoices,
} from './takeoverText'
import { useTakeoverJob } from './useTakeoverJob'

type View = 'loading' | 'intro' | 'running' | 'failed' | 'check' | 'taken'

function viewOf(loaded: boolean, job: TakeoverJob | null): View {
  if (!loaded) return 'loading'
  if (job === null) return 'intro'
  if (job.state === 'running') return 'running'
  if (job.state === 'failed' || job.result === null) return 'failed'
  return job.kind === 'takeover' ? 'taken' : 'check'
}

/**
 * "Übernehmen …" fuer eine Verbindung zu Radarr oder Sonarr, in Schritten: erst was passiert und die Warnung, dann die
 * Pruefung mit ihrem Fortschritt, dann ihr Ergebnis mit Zuordnungen und Bestaetigungen, dann die Uebernahme mit
 * Fortschritt und Ergebnis. Geaendert wird erst mit "Übernehmen und Verbindung beenden". Das Fenster darf zu, waehrend
 * etwas laeuft; der Server arbeitet weiter, und beim naechsten Oeffnen steht der laufende Auftrag wieder da.
 *
 * Gewaehlte Ordner gelten, bis das Fenster zugeht: Jede neue Pruefung schickt sie mit. Die Bestaetigungen gehoeren zum
 * Ergebnis einer Pruefung und beginnen bei jeder neuen leer.
 *
 * Bei Sonarr (S6) heissen die Saetze Serien und Folgendateien, und nach dem Speichern liest nexcrate die Serienordner
 * ein (Phase `reading_folders`) und legt die release.nex an.
 */
export function TakeoverDialog({ source, onClose, onTaken }: { source: Source; onClose: () => void; onTaken: () => void }) {
  const { t, i18n } = useTranslation()
  const { job, loaded, starting, error, lost, start } = useTakeoverJob(source.id)
  const [pressed, setPressed] = useState<'check' | 'takeover' | null>(null)
  const [chosen, setChosen] = useState<ChosenFolders>({})
  // Was die letzte angenommene Pruefung mitbekam. Weicht `chosen` davon ab, muss erst neu geprueft werden.
  const [sentChosen, setSentChosen] = useState<ChosenFolders>({})
  const [picking, setPicking] = useState<string | null>(null)
  const [choiceState, setChoiceState] = useState<{ jobId: number | null; choices: TakeoverChoices }>({ jobId: null, choices: DEFAULT_CHOICES })
  const blockId = useId()
  const onTakenRef = useRef(onTaken)
  onTakenRef.current = onTaken

  // Die App steht an der Verbindung, also auch schon vor der ersten Pruefung.
  const series = source.app === 'sonarr'
  // Seit Musik M6: eine Verbindung zu Lidarr.
  const music = source.app === 'lidarr'
  const kind = takeoverKind(source.app)
  const view = viewOf(loaded, job)
  const result = job?.result ?? null
  const choices = job !== null && choiceState.jobId === job.id ? choiceState.choices : DEFAULT_CHOICES
  const block = view === 'check' && result !== null ? takeoverBlock(result, choices, !sameChosen(chosen, sentChosen)) : null
  const number = (value: number) => formatNumber(value, i18n.language)

  // Fertig uebernommen: Die Seite dahinter laedt neu und zeigt die Verbindung als uebernommen.
  const takenId = view === 'taken' && job !== null ? job.id : null
  useEffect(() => {
    if (takenId !== null) onTakenRef.current()
  }, [takenId])

  function choose(change: Partial<TakeoverChoices>) {
    if (job !== null) setChoiceState({ jobId: job.id, choices: { ...choices, ...change } })
  }

  async function check() {
    if (starting) return
    const snapshot = chosen
    const mappings = chosenMappings(snapshot)
    setPressed('check')
    if (await start({ kind: 'check', body: mappings.length > 0 ? { mappings } : undefined })) setSentChosen(snapshot)
  }

  async function takeOver() {
    if (starting || result === null || block !== null) return
    setPressed('takeover')
    await start({ kind: 'takeover', body: takeoverRequest(result, choices) })
  }

  function close() {
    if (!starting) onClose()
  }

  const startProblem = error !== null && view !== 'running' ? <FormMessage>{errorText(t, error)}</FormMessage> : null
  const cancel = (
    <Button variant="ghost" onClick={close} disabled={starting}>
      {t('common.actions.cancel')}
    </Button>
  )
  const closeButton = (primary: boolean) => (
    <Button variant={primary ? 'primary' : 'ghost'} onClick={close} disabled={starting}>
      {t('common.actions.close')}
    </Button>
  )
  const recheck = (primary: boolean) => (
    <Button variant={primary ? 'primary' : 'ghost'} onClick={() => void check()} loading={starting && pressed === 'check'} disabled={starting}>
      {t('import.takeover.recheck')}
    </Button>
  )
  const warning = music ? t('import.takeover.music.warning') : series ? t('import.takeover.series.warning') : t('import.takeover.warning')

  let body = null
  let footer = null
  if (view === 'loading') {
    body = (
      <p className="flex items-center gap-2 py-2 text-sm text-mist-500" role="status">
        <Spinner />
        {t('common.loading')}
      </p>
    )
    footer = cancel
  } else if (view === 'intro') {
    body = (
      <div className="flex flex-col gap-4">
        {lost && <FormMessage tone="info">{t('import.takeover.lost')}</FormMessage>}
        <ul className="flex flex-col gap-2 text-sm text-mist-200">
          {[
            t('import.takeover.intro.versions', { name: source.name }),
            music ? t('import.takeover.music.intro.noRead') : series ? t('import.takeover.series.intro.noRead') : t('import.takeover.intro.noRead'),
            music ? t('import.takeover.music.intro.loads') : series ? t('import.takeover.series.intro.loads') : t('import.takeover.intro.loads'),
            // Library from disk: the takeover writes a release.nex into every movie folder afterwards. For Sonarr it
            // reads the series folders first (decision 8).
            music ? t('import.takeover.music.intro.companion') : series ? t('import.takeover.series.intro.companion') : t('import.takeover.intro.companion'),
          ].map((line) => (
            <li key={line} className="flex items-start gap-2">
              <Symbol name="check" className="mt-0.5 h-4 w-4 shrink-0 text-ok-500" />
              <span className="min-w-0 wrap-anywhere">{line}</span>
            </li>
          ))}
        </ul>
        <WarningNote>{warning}</WarningNote>
        <p className="text-sm text-mist-400">{t('import.takeover.intro.check')}</p>
        {startProblem}
      </div>
    )
    footer = (
      <>
        {cancel}
        <Button onClick={() => void check()} loading={starting} disabled={starting}>
          {t('import.takeover.check')}
        </Button>
      </>
    )
  } else if (view === 'running' && job !== null) {
    const progress = takeoverProgress(t, job, i18n.language, kind)
    body = (
      <div className="flex flex-col gap-3">
        <h3 className="text-sm font-semibold text-mist-100">{job.kind === 'takeover' ? t('import.takeover.running.takeover') : t('import.takeover.running.check')}</h3>
        <p className="flex items-center gap-2 text-sm text-info-500" role="status">
          <Spinner />
          {phaseText(t, job.phase, kind)}
        </p>
        {progress !== null && (
          <div className="flex flex-col gap-1.5">
            <ProgressBar value={progress.value} label={progress.label} />
            <p className="text-xs text-mist-500 tabular-nums">{progress.text}</p>
          </div>
        )}
        <p className="text-xs text-mist-500">{t('import.takeover.running.hint')}</p>
        {error !== null && <FormMessage>{errorText(t, error)}</FormMessage>}
      </div>
    )
    footer = closeButton(false)
  } else if (view === 'failed' && job !== null) {
    const reason = jobReasonText(t, job, source)
    body = (
      <div className="flex flex-col gap-3">
        <FormMessage>{job.kind === 'takeover' ? t('import.takeover.failed.takeover') : t('import.takeover.failed.check')}</FormMessage>
        <p className="text-sm wrap-anywhere text-mist-200">{jobErrorText(t, job, source)}</p>
        {reason !== null && <p className="text-sm wrap-anywhere text-mist-400">{reason}</p>}
        {startProblem}
      </div>
    )
    footer = (
      <>
        {closeButton(false)}
        {recheck(true)}
      </>
    )
  } else if (view === 'check' && result !== null) {
    body = (
      <div className="flex flex-col gap-4">
        {music ? (
          <MusicTakeoverCheck result={result} choices={choices} onChoose={choose} chosen={chosen} sentChosen={sentChosen} onPick={setPicking} />
        ) : (
          <TakeoverCheck result={result} choices={choices} onChoose={choose} chosen={chosen} sentChosen={sentChosen} onPick={setPicking} />
        )}
        {startProblem}
        {block !== null && (
          <p id={blockId} className="text-sm text-mist-300">
            {blockText(t, block, kind)}
          </p>
        )}
      </div>
    )
    footer = (
      <>
        {cancel}
        {recheck(false)}
        <Button
          onClick={() => void takeOver()}
          loading={starting && pressed === 'takeover'}
          disabled={starting || block !== null}
          aria-describedby={block !== null ? blockId : undefined}
        >
          {t('import.takeover.submit')}
        </Button>
      </>
    )
  } else if (view === 'taken' && result !== null) {
    const taken = result.taken
    const unclear = series ? (result.unclear ?? 0) : 0
    body = (
      <div className="flex flex-col gap-4">
        <FormMessage tone="ok">
          {music
            ? t('import.takeover.music.taken.done', { name: source.name })
            : series
              ? t('import.takeover.series.taken.done', { name: source.name })
              : t('import.takeover.taken.done', { name: source.name })}
        </FormMessage>
        {taken !== null && (
          <TakeoverFigures
            items={[
              { label: music ? t('import.takeover.music.taken.versions') : t('import.takeover.taken.versions'), value: taken.versions },
              { label: music ? t('import.takeover.music.taken.withFile') : t('import.takeover.taken.withFile'), value: taken.with_file },
              { label: music ? t('import.takeover.music.taken.missing') : t('import.takeover.taken.missing'), value: taken.missing },
              // Bei Sonarr stehen die Titel schon in der Bibliothek, der Import hat sie angelegt.
              ...(series || music ? [] : [{ label: t('import.takeover.taken.titles'), value: taken.titles }]),
            ]}
          />
        )}
        {music ? (
          <p className="text-sm text-mist-400">{t('import.takeover.music.taken.folders')}</p>
        ) : series ? (
          <p className="text-sm text-mist-400">{t('import.takeover.series.taken.folders')}</p>
        ) : (
          <p className="text-sm text-mist-400">{t('import.takeover.taken.tmdb')}</p>
        )}
        {unclear > 0 && <p className="text-sm text-mist-300">{t('import.takeover.series.counts.unclear', { count: unclear, value: number(unclear) })}</p>}
        {companionRows(result.companions).length > 0 && (
          <div className="flex flex-col gap-1">
            <h3 className="text-sm font-semibold text-mist-100">{t('import.takeover.taken.companions')}</h3>
            <ul className="flex flex-col gap-0.5 text-sm text-mist-300">
              {companionRows(result.companions).map((row) => (
                <li key={row.state}>{companionCountText(t, row.state, row.count, i18n.language)}</li>
              ))}
            </ul>
          </div>
        )}
        <WarningNote>{warning}</WarningNote>
      </div>
    )
    footer = closeButton(true)
  }

  return (
    <>
      <Dialog open wide title={t('import.takeover.title', { name: source.name })} onClose={close} footer={footer}>
        {body}
      </Dialog>
      {picking !== null && (
        <FolderPicker
          title={t('import.takeover.roots.pickerTitle', { remote: picking })}
          start={chosen[picking] ?? result?.roots.find((root) => root.remote === picking)?.local ?? null}
          onClose={() => setPicking(null)}
          onTake={(path) => {
            setChosen((current) => ({ ...current, [picking]: path }))
            setPicking(null)
          }}
        />
      )}
    </>
  )
}
