import { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { namingApi } from '../../api/naming'
import type { EpisodeNumbering, MultiEpisodeStyle, Naming, SeriesNamingPatterns, SeriesNamingPreview, SeriesNamingVersion } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage, Section, SelectField } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { SonarrNamingDialog } from '../import/SonarrNaming'
import { Loading, PatternInput, TokenList } from './PatternFields'
import { insertToken } from './patternDraft'
import { SERIES_PATTERNS, type SeriesPattern, withAddition } from './seriesNaming'

const STYLES: MultiEpisodeStyle[] = ['prefixed_range', 'range', 'scene', 'repeat', 'extend', 'duplicate']
const SERVERS = ['plex', 'jellyfin', 'emby'] as const
const PREVIEW_DELAY_MS = 300

type Draft = Record<SeriesPattern, string> & { multi_episode_style: MultiEpisodeStyle }

function draftOf(naming: Naming): Draft | null {
  const series = naming.series
  if (!series) return null
  return {
    series_folder: series.series_folder,
    season_folder: series.season_folder,
    specials_folder: series.specials_folder,
    episode_file: series.episode_file,
    daily_file: series.daily_file,
    // Ein Server von vor Anime A5 schickt kein Anime-Muster; dann steht die Vorgabe da.
    anime_file: series.anime_file ?? series.defaults.anime_file ?? '',
    multi_episode_style: series.multi_episode_style,
  }
}

function useSeriesPreview(draft: Draft | null, numbering: EpisodeNumbering, umlauts: string) {
  const [preview, setPreview] = useState<{ result: SeriesNamingPreview | null; error: unknown }>({ result: null, error: null })
  useEffect(() => {
    if (draft === null || SERIES_PATTERNS.some((field) => draft[field].trim() === '')) return
    const abort = new AbortController()
    const timer = setTimeout(() => {
      namingApi.seriesPreview({ ...draft, umlauts, episode_numbering: numbering }, abort.signal).then(
        (result) => {
          if (!abort.signal.aborted) setPreview({ result, error: null })
        },
        (error: unknown) => {
          if (!abort.signal.aborted) setPreview({ result: null, error })
        },
      )
    }, PREVIEW_DELAY_MS)
    return () => {
      clearTimeout(timer)
      abort.abort()
    }
  }, [draft, numbering, umlauts])
  return preview
}

/**
 * Die Benennung fuer Serien (S4.2): fuenf Muster mit Sonarrs Platzhaltern, der Stil fuer Mehrfachfolgen, fertige Zusaetze
 * fuer den Serienordner je Medienserver und eine Vorschau mit erfundenen Folgen, wahlweise nach TMDB oder TVDB
 * nummeriert. Darunter je Serienfassung die Nummern im Namen und eigene Muster. Die Umlaut-Regel gilt fuer Filme und
 * Serien gemeinsam und steht bei den Filmen.
 */
export function SeriesNamingSection() {
  const { t } = useTranslation()
  const notify = useNotice()
  const [naming, setNaming] = useState<Naming | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [numbering, setNumbering] = useState<EpisodeNumbering>('tmdb')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const cursor = useRef<{ field: SeriesPattern; position: number | null }>({ field: 'episode_file', position: null })
  const preview = useSeriesPreview(draft, numbering, naming?.umlauts ?? 'keep')

  useEffect(() => {
    let current = true
    namingApi.get().then(
      (result) => {
        if (!current) return
        setNaming(result)
        setDraft(draftOf(result))
      },
      (error: unknown) => {
        if (current) setLoadError(error)
      },
    )
    return () => {
      current = false
    }
  }, [])

  if (naming === null || draft === null || !naming.series) {
    return (
      <Section title={t('settings.files.seriesNaming.title')} intro={t('settings.files.seriesNaming.intro')}>
        {loadError !== null ? <FormMessage>{errorText(t, loadError)}</FormMessage> : <Loading />}
      </Section>
    )
  }

  const saved = naming
  const series = naming.series
  const current = draft
  const additions = Object.values(series.folder_additions)
  const empty = SERIES_PATTERNS.some((field) => current[field].trim() === '')
  const original = draftOf(saved)
  const changed = original === null || SERIES_PATTERNS.some((field) => current[field] !== original[field]) || current.multi_episode_style !== original.multi_episode_style
  const isDefault = SERIES_PATTERNS.every((field) => current[field] === series.defaults[field])

  function change(field: SeriesPattern, value: string, position: number | null) {
    cursor.current = { field, position }
    setDraft({ ...current, [field]: value })
  }

  function insert(token: string) {
    const { field, position } = cursor.current
    const next = insertToken(current[field], position, token)
    cursor.current = { field, position: next.position }
    setDraft({ ...current, [field]: next.value })
  }

  async function save() {
    setBusy(true)
    setProblem(null)
    try {
      const result = await namingApi.save({ movie_folder: saved.movie_folder, movie_file: saved.movie_file, umlauts: saved.umlauts, series: current })
      setNaming(result)
      setDraft(draftOf(result))
      notify(t('settings.files.seriesNaming.saved'))
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  function versionChanged(entry: SeriesNamingVersion) {
    setNaming((before) =>
      before === null || !before.series ? before : { ...before, series: { ...before.series, versions: before.series.versions.map((item) => (item.version_id === entry.version_id ? entry : item)) } },
    )
  }

  return (
    <>
      <Section title={t('settings.files.seriesNaming.title')} intro={t('settings.files.seriesNaming.intro')}>
        {SERIES_PATTERNS.map((field) => (
          <PatternInput
            key={field}
            label={patternLabel(t, field)}
            value={current[field]}
            onChange={(value, position) => change(field, value, position)}
            onCursor={(position) => {
              cursor.current = { field, position }
            }}
          />
        ))}
        <div className="flex flex-col gap-2">
          <p className="text-sm font-medium text-mist-300">{t('settings.files.seriesNaming.additions')}</p>
          <p className="text-xs text-mist-500">{t('settings.files.seriesNaming.additionsHint')}</p>
          <div className="flex flex-wrap gap-2">
            {SERVERS.map((server) => (
              <Button key={server} size="sm" variant="ghost" onClick={() => setDraft({ ...current, series_folder: withAddition(current.series_folder, series.folder_additions[server], additions) })}>
                {serverLabel(t, server)}
              </Button>
            ))}
          </div>
        </div>
        <TokenList tokens={series.tokens} onInsert={insert} />
        <SelectField label={t('settings.files.seriesNaming.style')} value={current.multi_episode_style} onChange={(event) => setDraft({ ...current, multi_episode_style: event.target.value as MultiEpisodeStyle })}>
          {STYLES.map((style) => (
            <option key={style} value={style}>
              {styleLabel(t, style)}
            </option>
          ))}
        </SelectField>
        <SelectField label={t('settings.files.seriesNaming.previewNumbering')} value={numbering} onChange={(event) => setNumbering(event.target.value as EpisodeNumbering)}>
          <option value="tmdb">{t('settings.files.seriesNaming.numberingTmdb')}</option>
          <option value="tvdb">{t('settings.files.seriesNaming.numberingTvdb')}</option>
        </SelectField>
        <SeriesPreviewBox preview={preview} empty={empty} />
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => void save()} loading={busy} disabled={!changed || empty}>
            {t('common.actions.save')}
          </Button>
          <Button variant="ghost" onClick={() => setDraft({ ...current, ...series.defaults })} disabled={isDefault}>
            {t('settings.files.naming.reset')}
          </Button>
        </div>
        <p className="text-xs text-mist-500">{t('settings.files.seriesNaming.notes')}</p>
      </Section>
      <Section title={t('settings.files.seriesNaming.versionsTitle')} intro={t('settings.files.seriesNaming.versionsIntro')}>
        {series.versions.length === 0 ? (
          <p className="text-sm text-mist-500">{t('settings.files.seriesNaming.noVersions')}</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {series.versions.map((entry) => (
              <li key={entry.version_id} className="min-w-0">
                <SeriesVersionNaming entry={entry} onChanged={versionChanged} />
              </li>
            ))}
          </ul>
        )}
      </Section>
    </>
  )
}

function patternLabel(t: ReturnType<typeof useTranslation>['t'], field: SeriesPattern): string {
  switch (field) {
    case 'series_folder':
      return t('settings.files.seriesNaming.patterns.series_folder')
    case 'season_folder':
      return t('settings.files.seriesNaming.patterns.season_folder')
    case 'specials_folder':
      return t('settings.files.seriesNaming.patterns.specials_folder')
    case 'episode_file':
      return t('settings.files.seriesNaming.patterns.episode_file')
    case 'daily_file':
      return t('settings.files.seriesNaming.patterns.daily_file')
    default:
      return t('settings.files.seriesNaming.patterns.anime_file')
  }
}

function serverLabel(t: ReturnType<typeof useTranslation>['t'], server: (typeof SERVERS)[number]): string {
  switch (server) {
    case 'plex':
      return t('settings.files.seriesNaming.server.plex')
    case 'jellyfin':
      return t('settings.files.seriesNaming.server.jellyfin')
    default:
      return t('settings.files.seriesNaming.server.emby')
  }
}

function styleLabel(t: ReturnType<typeof useTranslation>['t'], style: MultiEpisodeStyle): string {
  switch (style) {
    case 'extend':
      return t('settings.files.seriesNaming.styles.extend')
    case 'duplicate':
      return t('settings.files.seriesNaming.styles.duplicate')
    case 'repeat':
      return t('settings.files.seriesNaming.styles.repeat')
    case 'scene':
      return t('settings.files.seriesNaming.styles.scene')
    case 'range':
      return t('settings.files.seriesNaming.styles.range')
    default:
      return t('settings.files.seriesNaming.styles.prefixed_range')
  }
}

function exampleLabel(t: ReturnType<typeof useTranslation>['t'], key: string): string {
  switch (key) {
    case 'episode':
      return t('settings.files.seriesNaming.examples.episode')
    case 'double':
      return t('settings.files.seriesNaming.examples.double')
    case 'tba':
      return t('settings.files.seriesNaming.examples.tba')
    case 'daily':
      return t('settings.files.seriesNaming.examples.daily')
    case 'special':
      return t('settings.files.seriesNaming.examples.special')
    case 'special_numbering':
      return t('settings.files.seriesNaming.examples.special_numbering')
    default:
      return t('settings.files.seriesNaming.examples.special_placeholder')
    case 'anime':
      return t('settings.files.seriesNaming.examples.anime')
  }
}

function SeriesPreviewBox({ preview, empty }: { preview: { result: SeriesNamingPreview | null; error: unknown }; empty: boolean }) {
  const { t } = useTranslation()
  return (
    <div aria-live="polite" className="flex flex-col gap-2 rounded-xl border border-ink-700 bg-ink-950/60 px-3.5 py-3">
      <h3 className="text-xs font-medium text-mist-500">{t('settings.files.seriesNaming.preview')}</h3>
      {empty ? (
        <p className="text-sm text-bad-500">{t('settings.files.naming.previewEmpty')}</p>
      ) : preview.error !== null ? (
        <p className="text-sm wrap-anywhere text-bad-500">{errorText(t, preview.error)}</p>
      ) : preview.result !== null ? (
        <dl className="flex flex-col gap-2 text-sm">
          <div className="flex min-w-0 flex-col">
            <dt className="text-xs text-mist-500">{t('settings.files.seriesNaming.patterns.series_folder')}</dt>
            <dd className="font-mono break-all text-accent-400">{preview.result.series_folder}</dd>
          </div>
          {preview.result.examples.map((example) => (
            <div key={example.key} className="flex min-w-0 flex-col">
              <dt className="text-xs text-mist-500">{exampleLabel(t, example.key)}</dt>
              <dd className="font-mono break-all text-accent-400">
                {example.season_folder}/{example.file}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <Loading />
      )}
    </div>
  )
}

/** Eine Serienfassung: Nummern im Namen nach TMDB oder TVDB und eigene Muster; ein leeres Muster ist die Vorgabe. */
function SeriesVersionNaming({ entry, onChanged }: { entry: SeriesNamingVersion; onChanged: (entry: SeriesNamingVersion) => void }) {
  const { t } = useTranslation()
  const notify = useNotice()
  const headingId = useId()
  const [open, setOpen] = useState(false)
  const [taking, setTaking] = useState(false)
  const [own, setOwn] = useState<SeriesNamingPatterns>(() => ({ ...emptyOwn(), ...stripNull(entry.own) }))
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const hasOwn = SERIES_PATTERNS.some((field) => entry.own[field])

  async function save(numbering: EpisodeNumbering, patterns: SeriesNamingPatterns) {
    setBusy(true)
    setProblem(null)
    try {
      const body = { ...Object.fromEntries(SERIES_PATTERNS.map((field) => [field, patterns[field].trim() === '' ? null : patterns[field]])), episode_numbering: numbering }
      const result = await namingApi.saveSeriesVersion(entry.version_id, body)
      onChanged(result)
      notify(t('settings.files.versionNaming.saved', { label: entry.label }))
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  async function reset() {
    setBusy(true)
    setProblem(null)
    try {
      const result = await namingApi.resetSeriesVersion(entry.version_id)
      onChanged(result)
      setOwn(emptyOwn())
      notify(t('settings.files.versionNaming.resetDone', { label: entry.label }))
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3 rounded-xl border border-ink-700 bg-ink-900/60 p-3">
      <h3 id={headingId} className="text-sm font-semibold text-mist-100">
        {entry.label}
      </h3>
      <SelectField
        label={t('settings.files.seriesNaming.numbering')}
        hint={t('settings.files.seriesNaming.numberingHint')}
        value={entry.episode_numbering}
        disabled={busy}
        // Nur die Nummerierung aendert sich; getippte, noch nicht gespeicherte Muster gehen dabei nicht mit.
        onChange={(event) => void save(event.target.value as EpisodeNumbering, { ...emptyOwn(), ...stripNull(entry.own) })}
      >
        <option value="tmdb">{t('settings.files.seriesNaming.numberingTmdb')}</option>
        <option value="tvdb">{t('settings.files.seriesNaming.numberingTvdb')}</option>
      </SelectField>
      <p className="text-xs text-mist-500">{hasOwn ? t('settings.files.versionNaming.ownBadge') : t('settings.files.versionNaming.usesDefault')}</p>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="ghost" onClick={() => setOpen((value) => !value)} aria-expanded={open} aria-label={t('settings.files.versionNaming.editLabel', { label: entry.label })}>
          {t('settings.files.versionNaming.own')}
        </Button>
        {hasOwn && (
          <Button size="sm" variant="ghost" onClick={() => void reset()} disabled={busy} aria-label={t('settings.files.versionNaming.resetLabel', { label: entry.label })}>
            {t('settings.files.versionNaming.reset')}
          </Button>
        )}
        {/* Nur mit einer Verbindung zu Sonarr, wie der Knopf fuer Radarr bei den Filmen (Rueckmeldung 20.09.2026). */}
        {entry.source_id !== null && (
          <Button size="sm" variant="ghost" onClick={() => setTaking(true)} aria-label={t('settings.files.seriesNaming.fromSonarrLabel', { label: entry.label })}>
            <Symbol name="import" />
            {t('settings.files.seriesNaming.fromSonarr')}
          </Button>
        )}
      </div>
      {taking && entry.source_id !== null && (
        <SonarrNamingDialog
          sourceId={entry.source_id}
          versionId={entry.version_id}
          label={entry.label}
          onClose={() => setTaking(false)}
          onTaken={(saved) => {
            setTaking(false)
            setOwn({ ...emptyOwn(), ...stripNull(saved.own) })
            onChanged(saved)
            notify(t('import.sonarrNaming.taken', { label: saved.label }))
          }}
        />
      )}
      {open && (
        <div className="flex flex-col gap-3">
          <p className="text-xs text-mist-500">{t('settings.files.seriesNaming.ownHint')}</p>
          {SERIES_PATTERNS.map((field) => (
            <PatternInput key={field} label={patternLabel(t, field)} value={own[field]} onChange={(value) => setOwn({ ...own, [field]: value })} onCursor={() => undefined} />
          ))}
          <div>
            <Button size="sm" onClick={() => void save(entry.episode_numbering, own)} loading={busy} aria-label={t('settings.files.versionNaming.saveLabel', { label: entry.label })}>
              {t('common.actions.save')}
            </Button>
          </div>
        </div>
      )}
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
    </section>
  )
}

function emptyOwn(): SeriesNamingPatterns {
  return { series_folder: '', season_folder: '', specials_folder: '', episode_file: '', daily_file: '', anime_file: '' }
}

function stripNull(values: Partial<Record<SeriesPattern, string | null>>): Partial<SeriesNamingPatterns> {
  return Object.fromEntries(Object.entries(values).filter(([, value]) => typeof value === 'string')) as Partial<SeriesNamingPatterns>
}
