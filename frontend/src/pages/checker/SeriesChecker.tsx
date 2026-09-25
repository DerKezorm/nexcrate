import { useId, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { releasesApi, SERIES_NAMES_MAX } from '../../api/releases'
import type { SeriesCheck, SeriesCheckRelease, SeriesCheckRequest, SeriesCheckVersion, SeriesMatch, SeriesReleaseResult, Version } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, Field, FormMessage, Section } from '../../components/ui'
import { formatList, formatNumber } from '../../lib/format'
import { sizeText } from '../../lib/size'
import { Detail, Tile, TileHeader } from '../settings/parts'
import { gbToBytes, parseGb, releaseLanguage } from './checkerText'
import { FitBadge, ForNowHint, MatchedRules, RejectionList, UpgradeLine } from './resultParts'
import {
  episodeStateText,
  matchNotes,
  namesOf,
  readText,
  refusedText,
  releaseTypeText,
  seriesFitState,
  seriesNoteText,
  seriesRejectionText,
  viaText,
} from './seriesCheckerText'
import { TitlePicker, type PickedTitle } from './TitlePicker'

type Shown = { check: SeriesCheck; withSeries: boolean }

/**
 * Der Release-Pruefer fuer Serien im Reiter Fassungen: bis zu 50 Namen, wahlweise eine Serie aus der
 * Bibliothek und eine Groesse. Die Antwort sagt je Name, was nexcrate liest, welche Folgen er trifft
 * und je Fassung, ob er passt und was mit den Folgen geschaehe. Gesucht und geladen wird nichts.
 */
export function SeriesChecker({ versions, onSetUpProfile }: { versions: Version[]; onSetUpProfile: (version: Version) => void }) {
  const { t, i18n } = useTranslation()
  const namesId = useId()
  const [text, setText] = useState('')
  const [series, setSeries] = useState<PickedTitle | null>(null)
  const [size, setSize] = useState('')
  const [chosen, setChosen] = useState<number[]>([])
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [shown, setShown] = useState<Shown | null>(null)
  // Nur die neueste Pruefung zaehlt. Eine langsame alte ueberschreibt keine neuere.
  const generation = useRef(0)
  const names = namesOf(text)

  async function run() {
    if (names.length === 0) return setProblem(t('checker.series.missingNames'))
    if (names.length > SERIES_NAMES_MAX) return setProblem(t('checker.series.tooMany'))
    let sizeBytes: number | undefined
    if (size.trim() !== '' && names.length === 1) {
      const gb = parseGb(size)
      if (gb === null) return setProblem(t('checker.invalidSize'))
      sizeBytes = gbToBytes(gb)
    }
    const body: SeriesCheckRequest = { names }
    if (series !== null) body.title_id = series.id
    if (chosen.length > 0) body.version_ids = chosen
    if (sizeBytes !== undefined) body.size_bytes = sizeBytes

    const current = ++generation.current
    setBusy(true)
    setProblem(null)
    try {
      const check = await releasesApi.checkSeries(body)
      if (current !== generation.current) return
      setShown({ check, withSeries: series !== null })
    } catch (error) {
      if (current !== generation.current) return
      setShown(null)
      setProblem(errorText(t, error))
    } finally {
      if (current === generation.current) setBusy(false)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void run()
  }

  function versionOf(entry: SeriesCheckVersion): Version {
    return versions.find((version) => version.id === entry.version_id) ?? { id: entry.version_id, kind: 'series', label: entry.label, title_count: 0, has_profile: false, profile_line: null }
  }

  return (
    <Section title={t('checker.series.title')} intro={t('checker.series.intro')}>
      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <label htmlFor={namesId} className="text-sm font-medium text-mist-300">
            {t('checker.series.names')}
          </label>
          <textarea
            id={namesId}
            value={text}
            rows={4}
            spellCheck={false}
            aria-describedby={`${namesId}-hint`}
            onChange={(event) => setText(event.target.value)}
            className="w-full min-w-0 rounded-xl border border-ink-700 bg-ink-900 px-4 py-3 font-mono text-xs text-mist-100 focus:border-accent-500 focus:outline-none"
          />
          <p id={`${namesId}-hint`} className="text-xs text-mist-500">
            {t('checker.series.namesHint')}
          </p>
        </div>
        <div className="grid grid-cols-[minmax(0,1fr)] gap-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,11rem)]">
          <TitlePicker kind="series" value={series} onChange={setSeries} />
          {names.length <= 1 && (
            <Field
              label={t('checker.series.size')}
              hint={t('checker.series.sizeHint')}
              value={size}
              onChange={(event) => setSize(event.target.value)}
              inputMode="decimal"
              maxLength={12}
              autoComplete="off"
              className="w-full min-w-0 tabular-nums"
            />
          )}
        </div>
        {versions.length > 1 && <VersionChoice versions={versions} chosen={chosen} onChange={setChosen} />}
        <div>
          <Button type="submit" loading={busy}>
            {!busy && <Symbol name="search" />}
            {t('checker.series.submit')}
          </Button>
        </div>
      </form>
      {problem && <FormMessage>{problem}</FormMessage>}
      {shown && (
        <div className="flex flex-col gap-4 border-t border-ink-700 pt-4">
          {!shown.withSeries && <p className="text-xs text-mist-500">{t('checker.series.noSeries')}</p>}
          {shown.check.releases.length > 1 && <Ranking check={shown.check} />}
          <ul className="flex flex-col gap-3">
            {shown.check.releases.map((release, index) => (
              <li key={`${release.name}-${index}`} className="min-w-0">
                <ReleaseRow release={release} open={index === 0} onSetUpProfile={(entry) => onSetUpProfile(versionOf(entry))} language={i18n.language} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </Section>
  )
}

/** Welche Fassungen geprueft werden. Ohne Haken gelten alle. */
function VersionChoice({ versions, chosen, onChange }: { versions: Version[]; chosen: number[]; onChange: (value: number[]) => void }) {
  const { t } = useTranslation()
  const hintId = useId()
  return (
    <fieldset className="flex min-w-0 flex-col gap-1.5">
      <legend className="text-sm font-medium text-mist-300">{t('checker.series.versions')}</legend>
      <div className="flex flex-wrap gap-2">
        {versions.map((version) => {
          const picked = chosen.includes(version.id)
          return (
            <label
              key={version.id}
              className={
                'flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-1.5 text-sm transition-colors ' +
                (picked ? 'border-accent-500/60 bg-accent-500/10 text-mist-100' : 'border-ink-700 bg-ink-900/60 text-mist-300 hover:border-ink-600')
              }
            >
              <input
                type="checkbox"
                checked={picked}
                onChange={() => onChange(picked ? chosen.filter((id) => id !== version.id) : [...chosen, version.id])}
                className="h-4 w-4 accent-accent-500"
              />
              <span className="min-w-0 wrap-anywhere">{version.label}</span>
            </label>
          )
        })}
      </div>
      <p id={hintId} className="text-xs text-mist-500">
        {t('checker.series.versionsHint')}
      </p>
    </fieldset>
  )
}

/** Die Reihenfolge der Namen je Fassung, sobald mehr als einer eingegeben wurde. */
function Ranking({ check }: { check: SeriesCheck }) {
  const { t, i18n } = useTranslation()
  const labels = check.releases[0]?.versions ?? []
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-3 lg:grid-cols-[repeat(2,minmax(0,1fr))]">
      {labels.map((entry) => {
        const ranked = check.releases
          .map((release) => ({ name: release.name, result: release.versions.find((row) => row.version_id === entry.version_id)?.result ?? null }))
          .filter((row) => row.result !== null && row.result.accepted)
          .sort((left, right) => (left.result?.rank ?? 0) - (right.result?.rank ?? 0))
        return (
          <Tile key={entry.version_id}>
            <TileHeader title={t('checker.series.rankTitle', { label: entry.label })} />
            {ranked.length === 0 ? (
              <p className="text-sm text-mist-500">{t('checker.series.rankNone')}</p>
            ) : (
              <ol className="flex flex-col gap-1">
                {ranked.map((row) => (
                  <li key={row.name} className="flex min-w-0 items-baseline gap-2 text-sm">
                    <span className="shrink-0 text-xs text-mist-500 tabular-nums">{t('checker.series.rank', { rank: formatNumber(row.result?.rank ?? 0, i18n.language) })}</span>
                    <span className="min-w-0 font-mono text-xs wrap-anywhere text-mist-200">{row.name}</span>
                  </li>
                ))}
              </ol>
            )}
          </Tile>
        )
      })}
    </div>
  )
}

/** Ein Name mit allem, was nexcrate dazu sagt. Der erste steht offen da. */
function ReleaseRow({
  release,
  open,
  onSetUpProfile,
  language,
}: {
  release: SeriesCheckRelease
  open: boolean
  onSetUpProfile: (entry: SeriesCheckVersion) => void
  language: string
}) {
  const { t } = useTranslation()
  const refused = refusedText(t, release.parsed, language)
  return (
    <details open={open} className="min-w-0 rounded-xl border border-ink-700 bg-ink-900/40">
      <summary className="cursor-pointer px-4 py-3 font-mono text-xs wrap-anywhere text-mist-200">{release.name}</summary>
      <div className="flex flex-col gap-4 border-t border-ink-700 px-4 py-4">
        <ReadFacts release={release} language={language} />
        {refused && (
          <p className="flex items-start gap-2 text-sm text-bad-500">
            <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
            <span className="min-w-0">{refused}</span>
          </p>
        )}
        {release.match && <MatchBlock match={release.match} language={language} />}
        <ul className="grid grid-cols-[minmax(0,1fr)] gap-3 lg:grid-cols-[repeat(2,minmax(0,1fr))]">
          {release.versions.map((entry) => (
            <li key={entry.version_id} className="min-w-0">
              <VersionResult entry={entry} onSetUp={() => onSetUpProfile(entry)} language={language} />
            </li>
          ))}
        </ul>
      </div>
    </details>
  )
}

function ReadFacts({ release, language }: { release: SeriesCheckRelease; language: string }) {
  const { t } = useTranslation()
  const parsed = release.parsed
  const unknown = t('checker.parsed.unknown')
  const type = releaseTypeText(t, parsed)
  const languages = parsed.languages.length > 0 ? formatList(parsed.languages.map((value) => releaseLanguage(t, value)), language) : unknown
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold text-mist-300">{t('checker.series.readTitle')}</h3>
      <dl className="grid grid-cols-[minmax(0,1fr)] gap-3 rounded-xl border border-ink-700 bg-ink-900/60 p-3 sm:grid-cols-[repeat(2,minmax(0,1fr))] xl:grid-cols-[repeat(5,minmax(0,1fr))]">
        <Detail label={t('checker.series.read.series')}>{parsed.series_title ?? unknown}</Detail>
        <Detail label={t('checker.series.read.form')}>
          {readText(t, parsed, language)}
          {type && <span className="block text-xs text-mist-500">{type}</span>}
        </Detail>
        <Detail label={t('checker.parsed.quality')} mono>
          {parsed.quality ?? unknown}
        </Detail>
        <Detail label={t('checker.parsed.group')} mono>
          {parsed.group ?? unknown}
        </Detail>
        <Detail label={t('checker.parsed.languages')}>{languages}</Detail>
      </dl>
    </div>
  )
}

function MatchBlock({ match, language }: { match: SeriesMatch; language: string }) {
  const { t } = useTranslation()
  const via = viaText(t, match.via)
  const notes = matchNotes(t, match, language)
  return (
    <div className="flex flex-col gap-1.5">
      <h4 className="text-xs font-semibold text-mist-400">{t('checker.series.matchTitle')}</h4>
      {match.episodes.length === 0 ? (
        <p className="text-sm text-mist-500">{t('checker.series.match.none')}</p>
      ) : (
        <>
          <p className="text-sm text-mist-200">
            {[t('checker.series.match.count', { count: match.episodes.length, value: formatNumber(match.episodes.length, language) }), via].filter(Boolean).join(' ')}
          </p>
          <p className="font-mono text-xs wrap-anywhere text-mist-500">{match.episodes.map((episode) => episode.code).join(', ')}</p>
        </>
      )}
      {notes.map((note) => (
        <p key={note} className="flex items-start gap-2 text-xs text-mist-400">
          <Symbol name="info" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span className="min-w-0">{note}</span>
        </p>
      ))}
    </div>
  )
}

function VersionResult({ entry, onSetUp, language }: { entry: SeriesCheckVersion; onSetUp: () => void; language: string }) {
  const { t } = useTranslation()
  const hintId = useId()

  if (!entry.has_profile || entry.result === null) {
    return (
      <Tile className="h-full">
        <TileHeader title={entry.label} />
        <p className="text-sm text-mist-400">{t('checker.noProfile', { label: entry.label })}</p>
        <div className="mt-auto">
          <Button size="sm" onClick={onSetUp} aria-label={t('checker.setUpLabel', { label: entry.label })}>
            {t('checker.setUp')}
          </Button>
        </div>
      </Tile>
    )
  }

  const result = entry.result
  const state = seriesFitState(result)

  return (
    <Tile className="h-full">
      <TileHeader title={entry.label} sub={t('checker.score', { count: result.score, value: formatNumber(result.score, language) })}>
        {result.rank !== null && <Badge>{t('checker.series.rank', { rank: formatNumber(result.rank, language) })}</Badge>}
        <FitBadge state={state} describedBy={hintId} />
      </TileHeader>

      {state === 'forNow' && <ForNowHint id={hintId} text={result.below_target_in_group ? t('checker.series.forNowHint') : undefined} />}

      <RejectionList rejections={result.rejections} toText={(rejection, lang) => seriesRejectionText(t, rejection, lang)} />

      {result.notes.map((note, index) => (
        <p key={`${note.code}-${index}`} className="flex items-start gap-2 text-xs text-mist-400">
          <Symbol name="info" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span className="min-w-0">{seriesNoteText(t, note)}</span>
        </p>
      ))}

      {result.episodes.length > 0 && <EpisodeList result={result} language={language} />}

      {/* Eine einzelne Folge ohne Paket: die Zeile der Filme sagt dasselbe kuerzer. */}
      {result.accepted && result.episodes.length === 1 && result.episodes[0].upgrade && <UpgradeLine upgrade={result.episodes[0].upgrade} />}

      <MatchedRules matched={result.matched} />
    </Tile>
  )
}

/** Was mit jeder Folge geschaehe. Die Suche nach einer Serie zeigt dieselbe Liste in den Gruenden eines Releases. */
export function EpisodeList({ result, language }: { result: SeriesReleaseResult; language: string }) {
  const { t } = useTranslation()
  const fills = result.episodes.filter((row) => row.state === 'fills').length
  const replaces = result.replaces.files
  const summary =
    result.would_take === false || (fills === 0 && replaces === 0)
      ? t('checker.series.episodes.nothing')
      : replaces > 0
        ? t('checker.series.episodes.summary', {
            fills: formatNumber(fills, language),
            replaces: formatNumber(replaces, language),
            size: sizeText(t, result.replaces.size_bytes, language),
          })
        : t('checker.series.episodes.summaryNoReplace', { fills: formatNumber(fills, language) })
  return (
    <div className="flex flex-col gap-1.5">
      <h4 className="text-xs font-semibold text-mist-400">{t('checker.series.episodesTitle')}</h4>
      <ul className="flex flex-col gap-1">
        {result.episodes.map((row) => (
          <li key={row.episode_id} className="flex items-baseline justify-between gap-3 text-sm">
            <span className="shrink-0 font-mono text-xs text-mist-300">{row.code}</span>
            <span className={'min-w-0 text-right text-xs wrap-anywhere ' + (row.state === 'fills' || row.state === 'replaces' ? 'text-ok-500' : 'text-mist-500')}>
              {episodeStateText(t, row)}
            </span>
          </li>
        ))}
      </ul>
      <p className="text-sm text-mist-200">{summary}</p>
    </div>
  )
}
