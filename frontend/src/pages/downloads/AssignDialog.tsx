import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, errorText } from '../../api/client'
import { downloadsApi } from '../../api/downloads'
import type { Download, DownloadEpisodeChoice, DownloadFiles } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Spinner } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { sizeText } from '../../lib/size'
import { countingName } from '../checker/seriesCheckerText'
import { FinishDialog } from './FinishDialog'
import {
  type Choice,
  alsoHeld,
  initialChoices,
  OPEN_DECISIONS as OPEN,
  preselects,
  proposable,
  readingCode,
  readPart,
  stillMissing,
  twiceChosen,
  unreadableCount,
  withProposals,
  withReadings,
} from './assignChoices'

/** Die Gruende, warum eine Datei nicht offen ist, als Textschluessel. */
const DECISION_KEYS: Record<string, string> = {
  sample: 'downloads.assign.decision.sample',
  extra: 'downloads.assign.decision.extra',
  duplicate: 'downloads.assign.decision.duplicate',
  not_needed: 'downloads.assign.decision.not_needed',
  skipped: 'downloads.assign.decision.skipped',
  not_filed: 'downloads.assign.decision.not_filed',
}

/**
 * "Von Hand zuordnen" an einer Problemkarte (S4, Entscheidungen 35 bis 37 und 52): links jede Datei des Downloads mit
 * Pfad, Groesse, Laufzeit, dem Gelesenen und warum sie nicht offen ist; rechts die Folge oder Folgen. Oben, welche Folgen
 * noch ohne Datei sind. Abgelegte Dateien stehen grau dabei. Serie und Fassung stehen fest.
 *
 * Seit der Durchsicht: Abgelegt wird nur, was eine Folge hat; die uebrigen Dateien bleiben offen fuer spaeter. Beim Verdacht
 * auf eine andere Serie und bei einem Paket, das anders zaehlt als TMDB, ist nichts vorgewaehlt. Eine Doppelfolge geht nur
 * ganz. Ist eine Datei nicht besser als die vorhandene, fragt der Dialog einmal nach, auch "Nur die anderen ablegen".
 * Dateien ohne lesbaren Namen bekommen Folgen nur auf Knopfdruck (Antwort des Besitzers vom 17.09.2026).
 */
export function AssignDialog({ download, onClose, onDone }: { download: Download; onClose: () => void; onDone: (message: string) => void }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const [data, setData] = useState<DownloadFiles | null>(null)
  const [loadProblem, setLoadProblem] = useState<unknown>(null)
  const [choices, setChoices] = useState<Record<number, Choice>>({})
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const [notBetter, setNotBetter] = useState<string[] | null>(null)
  const [finishing, setFinishing] = useState(false)
  const code = download.problem?.code ?? null
  const unknown = typeof download.problem?.values?.unknown === 'string' ? download.problem.values.unknown : ''
  const counted = typeof download.problem?.values?.counted === 'string' ? download.problem.values.counted : ''
  const preselect = preselects(code, unknown)

  useEffect(() => {
    const abort = new AbortController()
    downloadsApi.files(download.id, abort.signal).then(
      (result) => {
        if (abort.signal.aborted) return
        setData(result)
        setChoices(initialChoices(result, preselect))
      },
      (error: unknown) => {
        if (!abort.signal.aborted) setLoadProblem(error)
      },
    )
    return () => abort.abort()
  }, [download.id, preselect])

  const bySeason = useMemo(() => {
    const groups = new Map<number, DownloadEpisodeChoice[]>()
    for (const episode of data?.episodes ?? []) {
      groups.set(episode.season, [...(groups.get(episode.season) ?? []), episode])
    }
    return [...groups.entries()].sort((a, b) => (a[0] === 0 ? 1 : b[0] === 0 ? -1 : a[0] - b[0]))
  }, [data])
  const episodeById = useMemo(() => new Map((data?.episodes ?? []).map((episode) => [episode.id, episode])), [data])
  const twice = twiceChosen(choices)
  const editable = (data?.files ?? []).filter((file) => OPEN.includes(file.decision))
  const filed = (data?.files ?? []).filter((file) => file.decision === 'filed')
  const chosenFiles = editable.filter((file) => (choices[file.key]?.episodes ?? []).some((id) => id !== null))
  const chosenCodes = new Set(
    Object.values(choices)
      .flatMap((choice) => choice.episodes)
      .filter((id): id is number => id !== null)
      .map((id) => episodeById.get(id)?.code ?? ''),
  )
  const missing = data !== null ? stillMissing(data, choices) : []
  const readable = editable.some((file) => file.episodes.length > 0)
  const unreadable = data !== null ? unreadableCount(data) : 0
  const proposals = data !== null ? proposable(data, choices) : 0
  const twiceCodes = [...twice].map((id) => episodeById.get(id)?.code ?? String(id)).join(', ')

  function update(key: number, episodes: (number | null)[]) {
    setChoices((current) => ({
      ...current,
      // Eine Haelfte gibt es nur mit genau einer Folge.
      [key]: { episodes: episodes.length > 0 ? episodes : [null], proposal: false, part: episodes.length === 1 ? (current[key]?.part ?? null) : null },
    }))
  }

  function choosePart(key: number, part: 1 | 2 | null) {
    setChoices((current) => ({ ...current, [key]: { ...(current[key] ?? { episodes: [null], proposal: false }), part } }))
  }

  async function submit(confirm: 'not_better'[] = [], without: string[] = []) {
    setBusy(true)
    setProblem(null)
    try {
      const files = chosenFiles
        .map((file) => {
          const episodeIds = (choices[file.key]?.episodes ?? []).filter((id): id is number => id !== null)
          const part = choices[file.key]?.part
          // Die Haelfte reist nur mit genau einer Folge mit.
          return part && episodeIds.length === 1 ? { key: file.key, episode_ids: episodeIds, part } : { key: file.key, episode_ids: episodeIds }
        })
        // "Nur die anderen ablegen": Dateien, deren Folgen nicht besser wuerden, bleiben offen.
        .filter((file) => !file.episode_ids.some((id) => without.includes(episodeById.get(id)?.code ?? '')))
      if (files.length === 0) {
        setBusy(false)
        return
      }
      await downloadsApi.assign(download.id, { files, confirm })
      onDone(t('downloads.assign.done'))
    } catch (error) {
      if (error instanceof ApiError && error.code === 'assignment_not_better' && confirm.length === 0) {
        const episodes = typeof error.values.episodes === 'string' ? error.values.episodes : ''
        setNotBetter(episodes.split(', ').filter((item) => item !== ''))
      } else {
        setProblem(error)
      }
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
      title={t('downloads.assign.title', { release: download.release.title })}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button variant="ghost" onClick={() => setFinishing(true)} disabled={busy || data === null}>
            {t('downloads.assign.finish')}
          </Button>
          <Button onClick={() => void submit()} loading={busy} disabled={data === null || chosenFiles.length === 0 || twice.size > 0}>
            {t('downloads.assign.submit', { count: chosenFiles.length, value: formatNumber(chosenFiles.length, language) })}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-mist-400">{t('downloads.assign.sub', { series: download.title.title, version: download.version.label })}</p>
        {code === 'other_series_suspected' && <p className="text-sm text-mist-300">{t('downloads.assign.otherSeriesHint')}</p>}
        {unknown !== '' && <p className="text-sm text-mist-300">{t('downloads.assign.unknownHint', { codes: unknown })}</p>}
        {counted !== '' && <p className="text-sm text-mist-300">{t('downloads.assign.countedHint', { scheme: countingName(t, counted) })}</p>}
        {data !== null && !preselect && readable && (
          <div>
            <Button variant="ghost" size="sm" onClick={() => setChoices((current) => withReadings(data, current))}>
              <Symbol name="check" />
              {t('downloads.assign.useRead')}
            </Button>
          </div>
        )}
        {data !== null && unreadable > 0 && (
          <div className="flex flex-col items-start gap-2">
            <p className="text-sm text-mist-300">{t('downloads.assign.unreadableHint', { count: unreadable, value: formatNumber(unreadable, language) })}</p>
            {proposals > 0 && (
              <Button variant="ghost" size="sm" onClick={() => setChoices((current) => withProposals(data, current))}>
                {t('downloads.assign.useProposals')}
              </Button>
            )}
          </div>
        )}
        {data !== null && (
          <p className="text-sm text-mist-200" role="status">
            {missing.length > 0 ? t('downloads.assign.missing', { codes: missing.join(', ') }) : t('downloads.assign.allCovered')}
          </p>
        )}
        {loadProblem !== null && <FormMessage>{errorText(t, loadProblem)}</FormMessage>}
        {data === null && loadProblem === null && (
          <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
            <Spinner />
            {t('downloads.assign.loading')}
          </p>
        )}
        {data !== null && (
          <ul className="flex flex-col gap-3" aria-label={t('downloads.assign.title', { release: download.release.title })}>
            {editable.map((file) => {
              const choice = choices[file.key] ?? { episodes: [null], proposal: false }
              const read = readingCode(file)
              const reason = DECISION_KEYS[file.decision]
              return (
                <li key={file.key} className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-3 rounded-xl border border-ink-700 bg-ink-900/60 p-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                  <div className="flex min-w-0 flex-col gap-1">
                    <p className="font-mono text-xs break-all text-mist-100">{file.path}</p>
                    <p className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-mist-500 tabular-nums">
                      <span>{sizeText(t, file.size_bytes, language)}</span>
                      {file.duration_seconds !== null && <span>{t('downloads.assign.duration', { minutes: formatNumber(Math.round(file.duration_seconds / 60), language) })}</span>}
                      <span>
                        {read !== null ? t('downloads.assign.read', { code: read }) : t('downloads.assign.readNothing')}
                        {read !== null && readPart(file) !== null && ' · ' + t('downloads.assign.partShort', { part: readPart(file) })}
                      </span>
                    </p>
                    {reason !== undefined && <p className="text-xs text-mist-400">{t(reason)}</p>}
                  </div>
                  <div className="flex min-w-0 flex-col gap-2">
                    {choice.episodes.map((selected, index) => {
                      const episode = selected !== null ? episodeById.get(selected) : undefined
                      const held = episode ? alsoHeld(episode, chosenCodes) : []
                      return (
                        <div key={index} className="flex min-w-0 flex-col gap-1">
                          <div className="flex min-w-0 items-center gap-2">
                            <select
                              aria-label={t('downloads.assign.episode') + ': ' + file.path}
                              value={selected === null ? '' : String(selected)}
                              onChange={(event) => {
                                const next = [...choice.episodes]
                                next[index] = event.target.value === '' ? null : Number(event.target.value)
                                update(file.key, next)
                              }}
                              className={
                                'min-w-0 flex-1 rounded-xl border bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none ' +
                                (selected !== null && twice.has(selected) ? 'border-bad-500' : 'border-ink-700')
                              }
                            >
                              <option value="">{t('downloads.assign.notFiled')}</option>
                              {bySeason.map(([season, episodes]) => (
                                <optgroup key={season} label={season === 0 ? t('downloads.assign.specials') : t('downloads.assign.season', { season })}>
                                  {episodes.map((item) => (
                                    <option key={item.id} value={item.id} disabled={item.state === 'filed'}>
                                      {item.name ? `${item.code} ${item.name}` : item.code}
                                    </option>
                                  ))}
                                </optgroup>
                              ))}
                            </select>
                            {choice.episodes.length > 1 && (
                              <Button
                                variant="ghost"
                                size="sm"
                                aria-label={t('downloads.assign.removeEpisodeLabel', { path: file.path })}
                                onClick={() => update(file.key, choice.episodes.filter((_value, position) => position !== index))}
                              >
                                <Symbol name="close" />
                              </Button>
                            )}
                          </div>
                          <p className="flex flex-wrap gap-2 text-xs">
                            {index === 0 && choice.proposal && <Badge tone="accent">{t('downloads.assign.proposal')}</Badge>}
                            {episode?.current_file && <span className="text-mist-400">{t('downloads.assign.replaces', { quality: episode.current_file.quality ?? '?' })}</span>}
                            {episode && !episode.watched && <span className="text-mist-500">{t('downloads.assign.notWatched')}</span>}
                          </p>
                          {held.length > 0 && <p className="text-xs text-bad-400">{t('downloads.assign.coversMore', { codes: held.join(', ') })}</p>}
                        </div>
                      )
                    })}
                    {choice.episodes.length === 1 && choice.episodes[0] !== null && (
                      // Zwei Dateien einer Folge, die TMDB als eine fuehrt: Teil 1 und Teil 2.
                      <select
                        aria-label={t('downloads.assign.partLabel', { path: file.path })}
                        value={choice.part ? String(choice.part) : ''}
                        onChange={(event) => choosePart(file.key, event.target.value === '1' ? 1 : event.target.value === '2' ? 2 : null)}
                        className="min-w-0 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
                      >
                        <option value="">{t('downloads.assign.partWhole')}</option>
                        <option value="1">{t('downloads.assign.partShort', { part: 1 })}</option>
                        <option value="2">{t('downloads.assign.partShort', { part: 2 })}</option>
                      </select>
                    )}
                    {choice.episodes[0] !== null && (
                      <div>
                        <Button variant="ghost" size="sm" onClick={() => update(file.key, [...choice.episodes, null])} aria-label={t('downloads.assign.anotherEpisodeLabel', { path: file.path })}>
                          <Symbol name="plus" />
                          {t('downloads.assign.anotherEpisode')}
                        </Button>
                      </div>
                    )}
                  </div>
                </li>
              )
            })}
            {filed.map((file) => (
              <li key={file.key} className="flex min-w-0 flex-col gap-1 rounded-xl border border-ink-700/60 p-3 text-mist-500">
                <p className="font-mono text-xs break-all">{file.path}</p>
                <p className="text-xs">{t('downloads.assign.filed', { code: file.episodes.map((episode) => episode.code).join(', ') })}</p>
              </li>
            ))}
          </ul>
        )}
        {data !== null && <p className="text-xs text-mist-500">{t('downloads.assign.openHint')}</p>}
        {data !== null && <p className="text-xs text-mist-500">{t('downloads.assign.sourceOnly')}</p>}
        {twice.size > 0 && <FormMessage>{t('downloads.assign.twice', { codes: twiceCodes })}</FormMessage>}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
      {notBetter !== null && (
        <Dialog
          open
          title={t('downloads.assign.notBetterTitle')}
          onClose={() => setNotBetter(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setNotBetter(null)}>
                {t('common.actions.cancel')}
              </Button>
              <Button
                variant="ghost"
                onClick={() => {
                  const codes = notBetter
                  setNotBetter(null)
                  void submit([], codes)
                }}
              >
                {t('downloads.assign.notBetterOthers')}
              </Button>
              <Button
                onClick={() => {
                  setNotBetter(null)
                  void submit(['not_better'])
                }}
              >
                {t('downloads.assign.notBetterConfirm')}
              </Button>
            </>
          }
        >
          <p className="text-sm text-mist-300">
            {t('downloads.assign.notBetter', { count: notBetter.length, value: formatNumber(notBetter.length, language), episodes: notBetter.join(', ') })}
          </p>
        </Dialog>
      )}
      {finishing && (
        <FinishDialog
          download={download}
          onClose={() => setFinishing(false)}
          onDone={(message) => {
            setFinishing(false)
            onDone(message)
          }}
        />
      )}
    </Dialog>
  )
}
