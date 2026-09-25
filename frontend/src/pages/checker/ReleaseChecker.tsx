import { useId, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { RELEASE_NAME_MAX, releasesApi } from '../../api/releases'
import type { ParsedRelease, ReleaseCheck, ReleaseCheckRequest, ReleaseCheckVersion, Version } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Button, Field, FormMessage, Section } from '../../components/ui'
import { formatList, formatNumber } from '../../lib/format'
import { Detail, Tile, TileHeader } from '../settings/parts'
import { fitState, gbToBytes, parseGb, releaseLanguage } from './checkerText'
import { TitlePicker, type PickedTitle } from './TitlePicker'
import { FitBadge, ForNowHint, MatchedRules, RejectionList, UpgradeLine } from './resultParts'

type Shown = { check: ReleaseCheck; withMovie: boolean; withSize: boolean }

/**
 * Der Release-Pruefer im Reiter Fassungen: ein Name, wahlweise ein Film aus der Bibliothek und eine
 * Groesse. Die Antwort sagt je Fassung, ob das Release passt, mit Punkten, Treffern, Gruenden und
 * ob es besser waere als die vorhandene Datei. Gesucht und geladen wird nichts.
 */
export function ReleaseChecker({ versions, onSetUpProfile }: { versions: Version[]; onSetUpProfile: (version: Version) => void }) {
  const { t } = useTranslation()
  const [name, setName] = useState('')
  const [movie, setMovie] = useState<PickedTitle | null>(null)
  const [size, setSize] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [shown, setShown] = useState<Shown | null>(null)
  // Nur die neueste Pruefung zaehlt. Eine langsame alte ueberschreibt keine neuere.
  const generation = useRef(0)

  async function run() {
    const cleanName = name.trim()
    if (cleanName === '') return setProblem(t('checker.missingName'))
    let sizeBytes: number | undefined
    if (size.trim() !== '') {
      const gb = parseGb(size)
      if (gb === null) return setProblem(t('checker.invalidSize'))
      sizeBytes = gbToBytes(gb)
    }
    const body: ReleaseCheckRequest = { name: cleanName }
    if (movie !== null) body.title_id = movie.id
    if (sizeBytes !== undefined) body.size_bytes = sizeBytes

    const current = ++generation.current
    setBusy(true)
    setProblem(null)
    try {
      const check = await releasesApi.check(body)
      if (current !== generation.current) return
      setShown({ check, withMovie: movie !== null, withSize: sizeBytes !== undefined })
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

  /** Die Fassung zum Ergebnis. Fehlt sie in der Liste (inzwischen neu), reicht, was die Antwort sagt. */
  function versionOf(entry: ReleaseCheckVersion): Version {
    return versions.find((version) => version.id === entry.version_id) ?? { id: entry.version_id, kind: 'movie', label: entry.label, title_count: 0, has_profile: false, profile_line: null }
  }

  return (
    <Section title={t('checker.title')} intro={t('checker.intro')}>
      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        <Field
          label={t('checker.name')}
          hint={t('checker.nameHint')}
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={RELEASE_NAME_MAX}
          autoComplete="off"
          spellCheck={false}
          className="w-full min-w-0 font-mono text-sm"
        />
        <div className="grid grid-cols-[minmax(0,1fr)] gap-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,11rem)]">
          <TitlePicker kind="movie" value={movie} onChange={setMovie} />
          <Field
            label={t('checker.size')}
            hint={t('checker.sizeHint')}
            value={size}
            onChange={(event) => setSize(event.target.value)}
            inputMode="decimal"
            maxLength={12}
            autoComplete="off"
            className="w-full min-w-0 tabular-nums"
          />
        </div>
        <div>
          <Button type="submit" loading={busy}>
            {!busy && <Symbol name="search" />}
            {t('checker.submit')}
          </Button>
        </div>
      </form>
      {problem && <FormMessage>{problem}</FormMessage>}
      {shown && (
        <div className="flex flex-col gap-4 border-t border-ink-700 pt-4">
          <ParsedFacts parsed={shown.check.parsed} />
          {shown.withSize && !shown.withMovie && <p className="text-xs text-mist-500">{t('checker.runtimeUnknown')}</p>}
          <h3 className="text-sm font-semibold text-mist-300">{t('checker.resultsTitle')}</h3>
          <ul className="grid grid-cols-[minmax(0,1fr)] gap-3 lg:grid-cols-[repeat(2,minmax(0,1fr))]">
            {shown.check.versions.map((entry) => (
              <li key={entry.version_id} className="min-w-0">
                <VersionResult entry={entry} onSetUp={() => onSetUpProfile(versionOf(entry))} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </Section>
  )
}

function ParsedFacts({ parsed }: { parsed: ParsedRelease }) {
  const { t, i18n } = useTranslation()
  const unknown = t('checker.parsed.unknown')
  const title = parsed.title ? (parsed.year ? t('checker.parsed.titleYear', { title: parsed.title, year: parsed.year }) : parsed.title) : unknown
  const languages = parsed.languages.length > 0 ? formatList(parsed.languages.map((value) => releaseLanguage(t, value)), i18n.language) : unknown
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold text-mist-300">{t('checker.parsedTitle')}</h3>
      <dl className="grid grid-cols-[minmax(0,1fr)] gap-3 rounded-xl border border-ink-700 bg-ink-900/60 p-3 sm:grid-cols-[repeat(2,minmax(0,1fr))] xl:grid-cols-[repeat(5,minmax(0,1fr))]">
        <Detail label={t('checker.parsed.title')}>{title}</Detail>
        <Detail label={t('checker.parsed.quality')} mono>
          {parsed.quality ?? unknown}
        </Detail>
        <Detail label={t('checker.parsed.group')} mono>
          {parsed.group ?? unknown}
        </Detail>
        <Detail label={t('checker.parsed.languages')}>{languages}</Detail>
        <Detail label={t('checker.parsed.edition')}>{parsed.edition ?? unknown}</Detail>
      </dl>
    </div>
  )
}

function VersionResult({ entry, onSetUp }: { entry: ReleaseCheckVersion; onSetUp: () => void }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
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
  const state = fitState(result)

  return (
    <Tile className="h-full">
      <TileHeader title={entry.label} sub={t('checker.score', { count: result.score, value: formatNumber(result.score, language) })}>
        <FitBadge state={state} describedBy={hintId} />
      </TileHeader>

      {state === 'forNow' && <ForNowHint id={hintId} />}

      <RejectionList rejections={result.rejections} />

      {/* Ein Release, das nicht passt, ersetzt nie eine Datei. Sonst stünde "Verbesserung" neben "Passt nicht". */}
      {result.accepted && result.upgrade && <UpgradeLine upgrade={result.upgrade} />}

      <MatchedRules matched={result.matched} />
    </Tile>
  )
}
