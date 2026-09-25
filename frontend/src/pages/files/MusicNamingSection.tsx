import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { namingApi } from '../../api/naming'
import type { MusicNamingPatterns, Naming } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage, Section } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { LidarrNamingDialog } from './LidarrNamingDialog'
import { Loading, PatternInput, TokenList } from './PatternFields'
import { insertToken } from './patternDraft'

const PREVIEW_DELAY_MS = 300
const MUSIC_PATTERNS: readonly (keyof MusicNamingPatterns)[] = ['artist_folder', 'album_folder', 'track_file', 'multi_disc_file']

function draftOf(naming: Naming): MusicNamingPatterns | null {
  const music = naming.music
  if (!music) return null
  return { artist_folder: music.artist_folder, album_folder: music.album_folder, track_file: music.track_file, multi_disc_file: music.multi_disc_file }
}

function useMusicPreview(draft: MusicNamingPatterns | null, umlauts: string) {
  const [preview, setPreview] = useState<{ result: MusicNamingPatterns | null; error: unknown }>({ result: null, error: null })
  useEffect(() => {
    if (draft === null || MUSIC_PATTERNS.some((field) => draft[field].trim() === '')) return
    const abort = new AbortController()
    const timer = setTimeout(() => {
      namingApi.musicPreview({ ...draft, umlauts }, abort.signal).then(
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
  }, [draft, umlauts])
  return preview
}

/**
 * Die Benennung fuer Musik (M4, Entscheidungen 14 bis 16): Kuenstlerordner, Albumordner, Datei eines Albums mit einem
 * Medium und mit mehreren, mit Lidarrs Platzhaltern und einer Vorschau fuer ein erfundenes Album. Ab Werk flach:
 * `Künstler/Album (Jahr)/1-01 Titel.flac`. Es gibt genau eine Musik-Fassung, deshalb keine Muster je Fassung.
 */
export function MusicNamingSection() {
  const { t } = useTranslation()
  const notify = useNotice()
  const [naming, setNaming] = useState<Naming | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [draft, setDraft] = useState<MusicNamingPatterns | null>(null)
  const [busy, setBusy] = useState(false)
  const [taking, setTaking] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const cursor = useRef<{ field: keyof MusicNamingPatterns; position: number | null }>({ field: 'track_file', position: null })
  const preview = useMusicPreview(draft, naming?.umlauts ?? 'keep')

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

  if (naming === null || draft === null || !naming.music) {
    return (
      <Section title={t('settings.files.musicNaming.title')} intro={t('settings.files.musicNaming.intro')}>
        {loadError !== null ? <FormMessage>{errorText(t, loadError)}</FormMessage> : <Loading />}
      </Section>
    )
  }

  const saved = naming
  const music = naming.music
  const current = draft
  const empty = MUSIC_PATTERNS.some((field) => current[field].trim() === '')
  const original = draftOf(saved)
  const changed = original === null || MUSIC_PATTERNS.some((field) => current[field] !== original[field])
  const isDefault = MUSIC_PATTERNS.every((field) => current[field] === music.defaults[field])

  function change(field: keyof MusicNamingPatterns, value: string, position: number | null) {
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
      const result = await namingApi.save({ movie_folder: saved.movie_folder, movie_file: saved.movie_file, umlauts: saved.umlauts, music: current })
      setNaming(result)
      setDraft(draftOf(result))
      notify(t('settings.files.musicNaming.saved'))
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section title={t('settings.files.musicNaming.title')} intro={t('settings.files.musicNaming.intro')}>
      {MUSIC_PATTERNS.map((field) => (
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
      <TokenList tokens={music.tokens} onInsert={insert} />
      <div className="flex flex-col gap-1 rounded-xl border border-ink-700 bg-ink-900/60 p-3" aria-live="polite">
        <p className="text-xs font-semibold text-mist-400">{t('settings.files.musicNaming.preview')}</p>
        {empty ? (
          <p className="text-sm text-mist-500">{t('settings.files.musicNaming.previewEmpty')}</p>
        ) : preview.error !== null ? (
          <FormMessage>{errorText(t, preview.error)}</FormMessage>
        ) : preview.result !== null ? (
          <>
            <p className="font-mono text-sm wrap-anywhere text-mist-100">
              {preview.result.artist_folder}/{preview.result.album_folder}/{preview.result.track_file}
            </p>
            <p className="font-mono text-sm wrap-anywhere text-mist-300">
              {preview.result.artist_folder}/{preview.result.album_folder}/{preview.result.multi_disc_file}
            </p>
          </>
        ) : (
          <Loading />
        )}
      </div>
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={() => void save()} loading={busy} disabled={!changed || empty}>
          {t('common.actions.save')}
        </Button>
        <Button variant="ghost" onClick={() => setDraft({ ...music.defaults })} disabled={isDefault}>
          {t('settings.files.naming.reset')}
        </Button>
        {/* Nur mit einer Verbindung zu Lidarr, wie bei Filmen und Serien (Rueckmeldung 20.09.2026). */}
        {music.source_id !== null && (
          <Button variant="ghost" onClick={() => setTaking(true)}>
            <Symbol name="import" />
            {t('settings.files.lidarrNaming.open')}
          </Button>
        )}
      </div>
      {taking && music.source_id !== null && (
        <LidarrNamingDialog
          sourceId={music.source_id}
          onClose={() => setTaking(false)}
          onTaken={(result) => {
            setTaking(false)
            setNaming(result)
            setDraft(draftOf(result))
            notify(t('settings.files.lidarrNaming.taken'))
          }}
        />
      )}
      <p className="text-xs text-mist-500">{t('settings.files.musicNaming.notes')}</p>
    </Section>
  )
}

function patternLabel(t: ReturnType<typeof useTranslation>['t'], field: keyof MusicNamingPatterns): string {
  switch (field) {
    case 'artist_folder':
      return t('settings.files.musicNaming.patterns.artist_folder')
    case 'album_folder':
      return t('settings.files.musicNaming.patterns.album_folder')
    case 'track_file':
      return t('settings.files.musicNaming.patterns.track_file')
    default:
      return t('settings.files.musicNaming.patterns.multi_disc_file')
  }
}
