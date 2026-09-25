import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { namingApi } from '../../api/naming'
import { sourcesApi } from '../../api/sources'
import type { EpisodeNumbering, SeriesNamingVersion, SeriesSourceNaming } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage } from '../../components/ui'
import { Loading, NoteLines } from '../files/PatternFields'
import { patternLabel, patternProblemText, seriesNamingNotes, styleText } from './sonarrNamingText'
import { SERIES_PATTERNS } from './takeoverText'

/**
 * Sonarrs Benennung, so wie nexcrate sie liest: die fuenf Muster, darunter je Muster nexcrates erster Einwand und die
 * Hinweise, was Sonarr anders macht, dazu Sonarrs Stil fuer mehrere Folgen in einer Datei. Genutzt im Dialog
 * "Benennung aus Sonarr" und in der Pruefung der Uebernahme.
 */
export function SeriesSourceNamingView({ naming }: { naming: SeriesSourceNaming }) {
  const { t } = useTranslation()
  const notes = seriesNamingNotes(t, naming.notes)
  const style = styleText(t, naming.multi_episode_style)
  return (
    <div className="flex flex-col gap-2.5">
      <dl className="flex flex-col gap-2.5">
        {SERIES_PATTERNS.map((which) => {
          const value = naming[which] ?? ''
          const problem = patternProblemText(t, naming.problems?.[which])
          return (
            <div key={which} className="flex min-w-0 flex-col gap-1">
              <dt className="text-xs text-mist-500">{patternLabel(t, which)}</dt>
              <dd className="rounded-lg border border-ink-700 bg-ink-950/60 px-3 py-2 font-mono text-sm break-all text-mist-100">
                {value.trim() === '' ? <span className="font-sans text-mist-500">{t('import.sonarrNaming.empty')}</span> : value}
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
        <div className="flex min-w-0 flex-col gap-1">
          <dt className="text-xs text-mist-500">{t('import.sonarrNaming.style')}</dt>
          <dd className={'text-sm wrap-anywhere ' + (style === null ? 'text-mist-400' : 'font-mono text-mist-100')}>{style ?? t('import.sonarrNaming.styleUnknown')}</dd>
        </div>
      </dl>
      <NoteLines notes={notes.general} />
    </div>
  )
}

/**
 * "Benennung übernehmen" neben einer Verbindung zu Sonarr: liest Sonarrs Benennung ueber die Verbindung, zeigt die
 * fuenf Muster und uebernimmt sie nach der Bestaetigung mit `PUT /api/naming/series/versions/{id}`. Kann nexcrate ein
 * Muster nicht annehmen (`can_take` false), bleibt Uebernehmen gesperrt, und der Einwand steht beim Muster.
 *
 * ⚠️ Dieselbe Route setzt auch die Nummerierung der Fassung. Deshalb liest das Fenster die gespeicherte Benennung mit
 * und schickt die Nummerierung unveraendert zurueck; sonst stuende eine Fassung mit TVDB-Nummern danach auf TMDB.
 */
export function SonarrNamingDialog({
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
  onTaken: (saved: SeriesNamingVersion) => void
}) {
  const { t } = useTranslation()
  const reasonId = useId()
  const [naming, setNaming] = useState<SeriesSourceNaming | null>(null)
  const [numbering, setNumbering] = useState<EpisodeNumbering>('tmdb')
  const [loadError, setLoadError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const refused = naming !== null && !naming.can_take

  useEffect(() => {
    let current = true
    sourcesApi.seriesNaming(sourceId).then(
      (result) => {
        if (current) setNaming(result)
      },
      (error: unknown) => {
        if (current) setLoadError(error)
      },
    )
    // Nur die Nummerierung der Fassung. Scheitert das, bleibt die Vorgabe.
    namingApi.get().then(
      (stored) => {
        const entry = stored.series?.versions.find((version) => version.version_id === versionId)
        if (current && entry !== undefined) setNumbering(entry.episode_numbering)
      },
      () => undefined,
    )
    return () => {
      current = false
    }
  }, [sourceId, versionId])

  async function take() {
    if (naming === null || !naming.can_take || busy) return
    setBusy(true)
    setProblem(null)
    try {
      const patterns = Object.fromEntries(SERIES_PATTERNS.map((which) => [which, naming[which]]))
      onTaken(await namingApi.saveSeriesVersion(versionId, { ...patterns, episode_numbering: numbering }))
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
      title={t('import.sonarrNaming.title', { label })}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void take()} loading={busy} disabled={naming === null || !naming.can_take} aria-describedby={refused ? reasonId : undefined}>
            {t('import.sonarrNaming.take')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-mist-300">{t('import.sonarrNaming.intro', { label })}</p>
        {loadError !== null ? (
          <FormMessage>{errorText(t, loadError)}</FormMessage>
        ) : naming === null ? (
          <Loading />
        ) : (
          <>
            <SeriesSourceNamingView naming={naming} />
            {refused && (
              <p id={reasonId} className="text-sm text-mist-300">
                {t('import.sonarrNaming.cannotTake', { label })}
              </p>
            )}
          </>
        )}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}
