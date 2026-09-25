import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { namingApi } from '../../api/naming'
import { sourcesApi } from '../../api/sources'
import type { MusicNamingPatterns, MusicSourceNaming, Naming } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage } from '../../components/ui'
import { MUSIC_PATTERNS, musicNamingNotes, musicPatternLabel, musicProblemText } from './musicNamingText'
import { Loading, NoteLines } from './PatternFields'

/**
 * Lidarrs Benennung, so wie nexcrate sie liest: die vier Muster, darunter je Muster nexcrates erster Einwand und die
 * Hinweise, was Lidarr anders macht.
 */
export function MusicSourceNamingView({ naming }: { naming: MusicSourceNaming }) {
  const { t } = useTranslation()
  const notes = musicNamingNotes(t, naming.notes)
  return (
    <div className="flex flex-col gap-2.5">
      <dl className="flex flex-col gap-2.5">
        {MUSIC_PATTERNS.map((which) => {
          const value = naming[which] ?? ''
          const problem = musicProblemText(t, naming.problems?.[which])
          return (
            <div key={which} className="flex min-w-0 flex-col gap-1">
              <dt className="text-xs text-mist-500">{musicPatternLabel(t, which)}</dt>
              <dd className="rounded-lg border border-ink-700 bg-ink-950/60 px-3 py-2 font-mono text-sm break-all text-mist-100">
                {value.trim() === '' ? <span className="font-sans text-mist-500">{t('settings.files.lidarrNaming.empty')}</span> : value}
              </dd>
              {problem !== null && <dd className="text-sm wrap-anywhere text-bad-500">{problem}</dd>}
              {(notes.patterns[which] ?? []).map((note) => (
                <dd key={note} className="flex items-start gap-1.5 text-xs wrap-anywhere text-mist-400">
                  <Symbol name="info" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-info-500" />
                  <span className="min-w-0">{note}</span>
                </dd>
              ))}
            </div>
          )
        })}
      </dl>
      <NoteLines notes={notes.general} />
    </div>
  )
}

/**
 * "Aus Lidarr übernehmen" bei der Benennung fuer Musik: liest Lidarrs Benennung ueber die Verbindung, zeigt die vier
 * Muster und uebernimmt sie nach der Bestaetigung mit `PUT /api/naming`. Kann nexcrate ein Muster nicht annehmen
 * (`can_take` false), bleibt Uebernehmen gesperrt, und der Einwand steht beim Muster.
 *
 * ⚠️ Dieselbe Route speichert die Benennung von Filmen und Serien mit. Deshalb schickt das Fenster den gespeicherten
 * Stand mit zurueck und aendert nur die Musik.
 */
export function LidarrNamingDialog({ sourceId, onClose, onTaken }: { sourceId: number; onClose: () => void; onTaken: (saved: Naming) => void }) {
  const { t } = useTranslation()
  const reasonId = useId()
  const [naming, setNaming] = useState<MusicSourceNaming | null>(null)
  const [stored, setStored] = useState<Naming | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const refused = naming !== null && !naming.can_take

  useEffect(() => {
    let current = true
    sourcesApi.musicNaming(sourceId).then(
      (result) => {
        if (current) setNaming(result)
      },
      (error: unknown) => {
        if (current) setLoadError(error)
      },
    )
    namingApi.get().then(
      (result) => {
        if (current) setStored(result)
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
    if (naming === null || stored === null || !naming.can_take || busy) return
    setBusy(true)
    setProblem(null)
    try {
      const music = Object.fromEntries(MUSIC_PATTERNS.map((which) => [which, naming[which]])) as MusicNamingPatterns
      onTaken(await namingApi.save({ movie_folder: stored.movie_folder, movie_file: stored.movie_file, umlauts: stored.umlauts, music }))
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
      title={t('settings.files.lidarrNaming.title')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void take()} loading={busy} disabled={naming === null || stored === null || !naming.can_take} aria-describedby={refused ? reasonId : undefined}>
            {t('settings.files.lidarrNaming.take')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-mist-300">{t('settings.files.lidarrNaming.intro')}</p>
        {loadError !== null ? (
          <FormMessage>{errorText(t, loadError)}</FormMessage>
        ) : naming === null ? (
          <Loading />
        ) : (
          <>
            <MusicSourceNamingView naming={naming} />
            {refused && (
              <p id={reasonId} className="text-sm text-mist-300">
                {t('settings.files.lidarrNaming.cannotTake')}
              </p>
            )}
          </>
        )}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}
