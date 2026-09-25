import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { numberingApi } from '../../api/numbering'
import type { Episode, NumberingCorrectionIn } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Button, Field, FormMessage, Toggle } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatNumber } from '../../lib/format'
import { CORRECTION_RANGE_MAX, correctionsFor, ownerNumber, readCorrectionNumber, readCountedNumber } from './correction'
import { episodeCode } from './seriesText'

/** So viele Zeilen zeigt die Vorschau, der Rest als Zahl. */
const PREVIEW_SHOWN = 3

/**
 * "Im Release heißt diese Folge" an einer aufgeklappten Folge (S3, Entscheidung 12). Der Server ersetzt beim Speichern
 * alle Korrekturen der Serie; deshalb holt die Seite erst die gespeicherten, tauscht die betroffenen Folgen aus und
 * schickt die ganze Liste zurueck.
 *
 * Seit B6 hat eine Anime-Serie ein Feld fuer die Durchzaehlnummer (Sonarr kann das nicht). Staffel und Folge stehen
 * dort leer, solange keine Korrektur sie traegt: vorbelegt mit TMDBs Nummern haette das Speichern der Durchzaehlnummer
 * nebenbei eine Korrektur von Staffel und Folge angelegt, die vor jeder Szene-Nummer kommt.
 */
export function EpisodeCorrection({
  titleId,
  episode,
  seasonEpisodes,
  anime = false,
  onSaved,
}: {
  titleId: number
  episode: Episode
  seasonEpisodes: readonly Episode[]
  anime?: boolean
  onSaved: () => void
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const notify = useNotice()
  const current = ownerNumber(episode)
  const currentNumbers = typeof current?.season === 'number' && typeof current.episode === 'number'
  const currentCounted = typeof current?.absolute === 'number' ? current.absolute : null
  const blank = anime && !currentNumbers
  const [open, setOpen] = useState(false)
  const [season, setSeason] = useState(blank ? '' : String(current?.season ?? episode.season_number))
  const [number, setNumber] = useState(blank ? '' : String(current?.episode ?? episode.number))
  const [end, setEnd] = useState(current?.episode_end != null ? String(current.episode_end) : '')
  const [counted, setCounted] = useState(currentCounted !== null ? String(currentCounted) : '')
  const [following, setFollowing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  const numbersGiven = season.trim() !== '' || number.trim() !== ''
  const seasonValue = numbersGiven ? readCorrectionNumber(season) : null
  const episodeValue = numbersGiven ? readCorrectionNumber(number) : null
  const endValue = end.trim() === '' ? null : readCorrectionNumber(end)
  const countedValue = anime && counted.trim() !== '' ? readCountedNumber(counted) : null
  const numbersValid = numbersGiven ? seasonValue !== null && episodeValue !== null : anime && countedValue !== null
  const valid =
    numbersValid &&
    (!anime || counted.trim() === '' || countedValue !== null) &&
    (end.trim() === '' || (endValue !== null && episodeValue !== null && endValue > episodeValue && endValue <= episodeValue + CORRECTION_RANGE_MAX))
  const planned = valid ? correctionsFor(episode, seasonEpisodes, { season: seasonValue, episode: episodeValue, episodeEnd: endValue, absolute: countedValue }, following) : []
  const byId = new Map(seasonEpisodes.map((item) => [item.id, item]))
  const preview = planned.map((item) => {
    const from = episodeCode((byId.get(item.episode_id) ?? episode).season_number, (byId.get(item.episode_id) ?? episode).number)
    if (typeof item.season !== 'number' || typeof item.episode !== 'number') return t('series.correction.previewOnlyAbsolute', { from, absolute: item.absolute })
    const to = episodeCode(item.season, item.episode, item.episode_end ?? null)
    return typeof item.absolute === 'number' ? t('series.correction.previewAbsolute', { from, to, absolute: item.absolute }) : t('series.correction.preview', { from, to })
  })
  const currentText = !current
    ? null
    : currentNumbers && currentCounted !== null
      ? t('series.correction.currentBoth', { code: episodeCode(current.season ?? 0, current.episode ?? 0, current.episode_end), absolute: currentCounted })
      : currentNumbers
        ? t('series.correction.current', { code: episodeCode(current.season ?? 0, current.episode ?? 0, current.episode_end) })
        : t('series.correction.currentAbsolute', { absolute: currentCounted })

  async function store(change: (items: NumberingCorrectionIn[]) => NumberingCorrectionIn[], done: string) {
    setBusy(true)
    setProblem(null)
    try {
      const stored = await numberingApi.get(titleId)
      // Die Durchzaehlnummer reist mit, sonst ginge sie beim Speichern einer anderen Folge verloren (B6).
      const items = stored.corrections.map((item) => ({ episode_id: item.episode_id, season: item.season, episode: item.episode, episode_end: item.episode_end, ...(typeof item.absolute === 'number' ? { absolute: item.absolute } : {}) }))
      await numberingApi.save(titleId, { corrections: change(items) })
      setOpen(false)
      notify(done)
      onSaved()
    } catch (error) {
      setProblem(errorText(t, error))
    } finally {
      setBusy(false)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!valid) return setProblem(t(anime ? 'series.correction.invalidAnime' : 'series.correction.invalid'))
    const touched = new Set(planned.map((item) => item.episode_id))
    void store((items) => [...items.filter((item) => !touched.has(item.episode_id)), ...planned], t('series.correction.saved'))
  }

  function remove() {
    void store((items) => items.filter((item) => item.episode_id !== episode.id), t('series.correction.removed'))
  }

  return (
    <div className="flex flex-col gap-2">
      <h4 className="text-sm font-semibold text-mist-200">{t('series.correction.title')}</h4>
      {currentText && <p className="text-sm text-mist-300">{currentText}</p>}
      {!open ? (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>
            <Symbol name="swap" />
            {t('series.correction.open')}
          </Button>
          {current && (
            <Button size="sm" variant="ghost" onClick={remove} loading={busy}>
              {t('series.correction.remove')}
            </Button>
          )}
        </div>
      ) : (
        <form onSubmit={submit} noValidate className="flex flex-col gap-3 rounded-xl border border-ink-700 bg-ink-900/60 p-3">
          <div className="grid grid-cols-[repeat(3,minmax(0,1fr))] gap-3">
            <Field label={t('series.correction.season')} inputMode="numeric" value={season} onChange={(event) => setSeason(event.target.value)} maxLength={4} className="w-full min-w-0 tabular-nums" />
            <Field label={t('series.correction.episode')} inputMode="numeric" value={number} onChange={(event) => setNumber(event.target.value)} maxLength={4} className="w-full min-w-0 tabular-nums" />
            <Field label={t('series.correction.episodeEnd')} inputMode="numeric" value={end} onChange={(event) => setEnd(event.target.value)} maxLength={4} className="w-full min-w-0 tabular-nums" />
          </div>
          <p className="text-xs text-mist-500">
            {t('series.correction.episodeEndHint')}
            {anime && ` ${t('series.correction.numbersHint')}`}
          </p>
          {anime && (
            <Field
              label={t('series.correction.absolute')}
              hint={t('series.correction.absoluteHint')}
              inputMode="numeric"
              value={counted}
              onChange={(event) => setCounted(event.target.value)}
              maxLength={4}
              className="w-full min-w-0 tabular-nums sm:w-40"
            />
          )}
          <Toggle label={t('series.correction.following')} hint={t('series.correction.followingHint')} checked={following} onChange={setFollowing} />
          {preview.length > 0 && (
            <ul role="status" className="flex flex-col gap-0.5 rounded-lg border border-info-500/30 bg-info-500/5 px-3 py-2 text-xs text-mist-300 tabular-nums">
              {preview.slice(0, PREVIEW_SHOWN).map((line) => (
                <li key={line}>{line}</li>
              ))}
              {preview.length > PREVIEW_SHOWN && (
                <li>{t('series.correction.previewMore', { count: preview.length - PREVIEW_SHOWN, value: formatNumber(preview.length - PREVIEW_SHOWN, language) })}</li>
              )}
            </ul>
          )}
          {problem && <FormMessage>{problem}</FormMessage>}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" size="sm" loading={busy}>
              {t('series.correction.save')}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setOpen(false)} disabled={busy}>
              {t('common.actions.cancel')}
            </Button>
          </div>
        </form>
      )}
      {!open && problem && <FormMessage>{problem}</FormMessage>}
    </div>
  )
}
