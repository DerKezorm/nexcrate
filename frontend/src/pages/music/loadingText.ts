import type { TFunction } from 'i18next'

import { ApiError, errorText } from '../../api/client'
import type { ArtistLoadState } from '../../api/types'

/** Die Zeile einer Kuenstlerkachel, solange sie nicht `ready` ist (Entscheidung 38). null, wenn nichts zu sagen ist. */
export function tileLoadingText(t: TFunction, loadState: ArtistLoadState, loadDone: number, loadTotal: number | null): string | null {
  if (loadState === 'ready') return null
  if (loadState === 'failed') return t('music.artist.loadingFailed')
  if (loadState === 'queued') return t('music.artist.loadingQueued')
  if (loadTotal !== null && loadTotal > 0) return t('music.artist.loadingCount', { done: loadDone, total: loadTotal })
  return t('music.artist.loading')
}

/** Der Fehlertext eines fehlgeschlagenen Ladens, aus `errors.json` wie jeder andere Fehler. */
export function loadErrorText(t: TFunction, code: string | null): string {
  if (!code) return t('music.artist.loadingFailed')
  return errorText(t, new ApiError(0, code))
}

/** Die Ladezeile auf der Kuenstlerseite: Schritt und Zahl (Entscheidung 39). */
export function artistLoadingLine(t: TFunction, loadState: ArtistLoadState, loadDone: number, loadTotal: number | null): string | null {
  if (loadState === 'ready' || loadState === 'failed') return null
  if (loadState === 'queued') return t('music.artist.loadingQueued')
  const hasTotal = loadTotal !== null && loadTotal > 0
  if (loadState === 'groups') return hasTotal ? t('music.artist.loadingGroups', { done: loadDone, total: loadTotal }) : t('music.artist.loading')
  if (loadState === 'releases') return hasTotal ? t('music.artist.loadingReleases', { done: loadDone, total: loadTotal }) : t('music.artist.loading')
  return t('music.artist.loading')
}

/** Der Schrittname fuer die Zeile am oberen Rand der Musik-Bibliothek, etwa "Ausgaben" waehrend `releases`. */
export function loadingStepWord(t: TFunction, step: string): string {
  if (step === 'releases') return t('music.loadingBar.stepReleases')
  if (step === 'groups') return t('music.loadingBar.stepGroups')
  return t('music.loadingBar.stepOther')
}
