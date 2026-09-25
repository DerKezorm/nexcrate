import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { musicApi } from '../../api/music'
import type { AlbumBlock, TitleVersion } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage, ProgressBar, Spinner } from '../../components/ui'
import { VersionChip } from '../../components/VersionChip'
import { formatNumber } from '../../lib/format'
import { albumQualityText } from '../../lib/musicSteps'
import { targetReasonSentence, releaseLine } from './albumText'

/**
 * Die Fassungskarte der Albumseite (M1.5.40): Zustand, "9 von 12 Titeln", die Zielausgabe mit ihrer Begruendung
 * (Entscheidung 27), der Knopf "Ausgabe wählen" (Entscheidung 41) und der rosa Hinweis, wenn eine groessere Ausgabe
 * verfuegbar ist (Entscheidung 29). Fehlen die Ausgaben noch, laedt die Karte sie einmal von selbst nach
 * (Entscheidung 20, "Der Besitzer geht vor").
 */
export function AlbumVersionCard({
  titleId,
  album,
  version,
  onOpenChoose,
  onLoaded,
  onSearchMissing,
}: {
  titleId: number
  album: AlbumBlock
  /** Die eine Fassung des Albums, oder null, solange sie nicht ueberwacht ist. */
  version: TitleVersion | null
  onOpenChoose: () => void
  /** Die Ausgaben sind (neu) geladen; die Seite soll den Titel neu holen. */
  onLoaded: () => void
  /** Seit dem 19.09.2026: die Albensuche starten, wenn Titel fehlen. Ohne: kein Knopf. */
  onSearchMissing?: () => void
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<unknown>(null)
  const attempted = useRef(false)

  // Neu laden, solange nichts geladen ist, oder solange die Fassung ein Ziel braucht, das die Regel noch nie
  // gesetzt hat (etwa eine Fassung, die erst ueber "Fassungen ändern" dazukam, nachdem die Ausgaben schon da waren:
  // `add_owner_version` setzt kein Ziel, das tut erst die Regel beim Laden, `services/music/loading.py:load_releases_of`).
  // `failed`: der Ladejob hat es aufgegeben, weil MusicBrainz ausgelastet blieb. Wer die Seite oeffnet, fragt auf
  // der eigenen Spur noch einmal; scheitert das, steht der Fehler mit dem Knopf da.
  const needsReleases = album.releases_state === 'none' || album.releases_state === 'heads' || album.releases_state === 'failed' || (version !== null && album.target === null)

  useEffect(() => {
    if (!needsReleases || attempted.current) return
    attempted.current = true
    setLoading(true)
    setLoadError(null)
    musicApi.loadReleases(titleId).then(
      () => {
        setLoading(false)
        onLoaded()
      },
      (error: unknown) => {
        setLoading(false)
        setLoadError(error)
      },
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [needsReleases, titleId])

  function retry() {
    attempted.current = false
    setLoadError(null)
  }

  const trackCounts = album.track_counts
  const wanted = trackCounts?.wanted ?? 0
  const present = trackCounts?.present ?? 0
  const reason = targetReasonSentence(t, album.target_reason, album.releases, language)
  const missingTracks = version?.state === 'incomplete' ? album.tracks.filter((track) => !track.present) : []
  // Die Ausgabe, deren Titel die Dateien genau abdecken (hier meist die, aus der sie kamen): ein Klick macht sie zum Ziel.
  const fitting = version?.state === 'incomplete' && album.actual !== null && album.actual.id !== album.target?.id && album.actual.track_count === present ? album.actual : null
  const [choosing, setChoosing] = useState(false)
  const [chooseError, setChooseError] = useState<unknown>(null)

  async function takeFitting(releaseId: number) {
    setChoosing(true)
    setChooseError(null)
    try {
      await musicApi.setTarget(titleId, { release_id: releaseId })
      onLoaded()
    } catch (error) {
      setChooseError(error)
    } finally {
      setChoosing(false)
    }
  }

  return (
    <div className="flex flex-col gap-4 rounded-2xl border border-ink-700 bg-ink-900/60 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">{t('title.album.target.title')}</h2>
        {version !== null && <VersionChip version={version} />}
      </div>

      {version === null ? (
        <p className="flex items-start gap-2 text-sm text-mist-400">
          <Symbol name="eyeOff" className="mt-0.5 h-4 w-4 shrink-0" />
          {t('title.album.notWatched')}
        </p>
      ) : (
        <>
          {wanted > 0 && (
            <div className="flex flex-col gap-1.5">
              <p className="text-sm text-mist-200 tabular-nums">{t('title.album.trackCounts', { present: formatNumber(present, language), wanted: formatNumber(wanted, language) })}</p>
              {present < wanted && <ProgressBar value={present / wanted} tone={version.state === 'upgrade' ? 'accent' : 'info'} label={t('title.album.trackCounts', { present, wanted })} />}
            </div>
          )}

          {missingTracks.length > 0 && (
            <div className="flex flex-col gap-2 rounded-xl border border-dashed border-bad-500/50 bg-bad-500/5 p-3">
              <p className="flex items-start gap-2 text-sm text-bad-500">
                <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
                <span>
                  {t('title.album.incomplete.title', { count: missingTracks.length })}{' '}
                  <span className="text-mist-300">{missingTracks.map((track) => track.name).join(', ')}</span>
                </span>
              </p>
              {fitting !== null && <p className="pl-6 text-sm text-mist-400">{t('title.album.incomplete.fitting', { release: releaseLine(fitting, language) })}</p>}
              <p className="pl-6 text-sm text-mist-400">{t('title.album.incomplete.complete')}</p>
              <div className="flex flex-wrap gap-2 pl-6">
                {onSearchMissing && (
                  <Button size="sm" onClick={onSearchMissing}>
                    <Symbol name="search" />
                    {t('title.album.incomplete.search')}
                  </Button>
                )}
                {fitting !== null && (
                  <Button size="sm" variant="ghost" loading={choosing} onClick={() => void takeFitting(fitting.id)}>
                    <Symbol name="layers" />
                    {t('title.album.incomplete.takeFitting')}
                  </Button>
                )}
              </div>
              {chooseError !== null && <FormMessage>{errorText(t, chooseError)}</FormMessage>}
            </div>
          )}

          {/* Musik M2: die Stufe der schlechtesten Datei, und warum das Album auf Bernstein steht. Nur Anzeige. */}
          {version.quality && (
            <p className="flex items-start gap-2 text-sm text-mist-300">
              <Symbol name={version.state === 'upgrade' ? 'arrowUp' : 'check'} className={'mt-0.5 h-4 w-4 shrink-0 ' + (version.state === 'upgrade' ? 'text-accent-400' : 'text-ok-500')} />
              <span>
                {t('title.album.quality', { step: albumQualityText(t, version.quality) })}
                {version.state === 'upgrade' && <span className="block text-mist-400">{t('title.album.upgradeHint')}</span>}
              </span>
            </p>
          )}

          {album.target !== null ? (
            <div className="flex flex-col gap-1">
              <p className="flex items-start gap-2 text-sm text-mist-300">
                <Symbol name="layers" className="mt-0.5 h-4 w-4 shrink-0 text-accent-400" />
                <span>
                  {t('title.album.target.line')} <strong className="font-semibold text-mist-100">{releaseLine(album.target, language)}</strong>
                </span>
              </p>
              {reason !== null && <p className="pl-6 text-sm text-mist-400">{reason}</p>}
            </div>
          ) : loading ? (
            <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
              <Spinner className="h-3.5 w-3.5" />
              {t('title.album.loadingReleases')}
            </p>
          ) : loadError !== null ? (
            <div className="flex flex-col items-start gap-2">
              <FormMessage>{errorText(t, loadError)}</FormMessage>
              <Button size="sm" variant="ghost" onClick={retry}>
                <Symbol name="refresh" />
                {t('common.actions.retry')}
              </Button>
            </div>
          ) : album.releases_state === 'tracks' ? (
            <p className="flex items-start gap-2 text-sm text-mist-500">
              <Symbol name="clock" className="mt-0.5 h-4 w-4 shrink-0" />
              {t('title.album.noTarget')}
            </p>
          ) : null}

          {album.releases.length > 0 && (
            <div>
              <Button variant="ghost" onClick={onOpenChoose}>
                <Symbol name="layers" />
                {t('title.album.chooseRelease.open')}
              </Button>
            </div>
          )}

          {album.suggestion !== null && (
            <FormMessage>
              <strong className="font-semibold">{t('title.album.suggestion.title')}</strong> {t('title.album.suggestion.text', { release: releaseLine(album.suggestion, language) })}
            </FormMessage>
          )}
        </>
      )}
    </div>
  )
}
