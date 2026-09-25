import { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { namingApi } from '../../api/naming'
import type { Naming, NamingPatterns, VersionNaming } from '../../api/types'
import { Button, FormMessage, Section, Switch } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { insertToken, type PatternCursor, type PatternField } from './patternDraft'
import { Loading, PatternInput, PreviewBox, TokenList } from './PatternFields'
import { usePatternPreview } from './usePatternPreview'
import { VersionNamingSection } from './VersionNamingSection'

function patternsOf(naming: NamingPatterns): NamingPatterns {
  return { movie_folder: naming.movie_folder, movie_file: naming.movie_file, umlauts: naming.umlauts }
}

function samePatterns(left: NamingPatterns, right: NamingPatterns): boolean {
  return left.movie_folder === right.movie_folder && left.movie_file === right.movie_file && left.umlauts === right.umlauts
}

/**
 * Die Standard-Benennung: Muster fuer Filmordner und Filmdatei, mit den Platzhaltern des Servers zum Einsetzen, einer
 * Vorschau, die beim Tippen mitlaeuft, und dem Schalter fuer Umlaute. Ob ein Muster gilt, sagt der Server: in der
 * Vorschau sofort, beim Speichern verbindlich. Leer geht nicht, das sagt die Seite selbst. Darunter die Benennung je
 * Fassung, sobald der Server sie kennt.
 */
export function NamingSection() {
  const { t } = useTranslation()
  const notify = useNotice()
  const emptyId = useId()
  const [naming, setNaming] = useState<Naming | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [draft, setDraft] = useState<NamingPatterns | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const cursor = useRef<PatternCursor>({ field: 'movie_file', position: null })
  const empty = draft !== null && (draft.movie_folder.trim() === '' || draft.movie_file.trim() === '')
  const preview = usePatternPreview(draft, empty)

  useEffect(() => {
    let current = true
    namingApi.get().then(
      (result) => {
        if (!current) return
        setNaming(result)
        setDraft(patternsOf(result))
      },
      (error: unknown) => {
        if (current) setLoadError(error)
      },
    )
    return () => {
      current = false
    }
  }, [])

  if (naming === null || draft === null) {
    return (
      <Section title={t('settings.files.naming.title')} intro={t('settings.files.naming.intro')}>
        {loadError !== null ? <FormMessage>{errorText(t, loadError)}</FormMessage> : <Loading />}
      </Section>
    )
  }

  const saved = naming
  const patterns = draft
  const changed = !samePatterns(patterns, patternsOf(saved))
  const isDefault = patterns.movie_folder === saved.defaults.movie_folder && patterns.movie_file === saved.defaults.movie_file
  const tokens = Array.isArray(saved.tokens) ? saved.tokens : []

  function change(field: PatternField, value: string, position: number | null) {
    cursor.current = { field, position }
    setDraft({ ...patterns, [field]: value })
  }

  function insert(token: string) {
    const { field, position } = cursor.current
    const next = insertToken(patterns[field], position, token)
    cursor.current = { field, position: next.position }
    setDraft({ ...patterns, [field]: next.value })
  }

  function versionChanged(entry: VersionNaming) {
    setNaming((current) =>
      current === null || !Array.isArray(current.versions)
        ? current
        : { ...current, versions: current.versions.map((item) => (item.version_id === entry.version_id ? entry : item)) },
    )
  }

  async function save() {
    if (busy) return
    setBusy(true)
    setProblem(null)
    try {
      const result = await namingApi.save(patterns)
      setNaming(result)
      setDraft(patternsOf(result))
      notify(t('settings.files.naming.saved'))
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Section title={t('settings.files.naming.title')} intro={t('settings.files.naming.intro')}>
        <PatternInput
          label={t('settings.files.naming.movieFolder')}
          value={patterns.movie_folder}
          onChange={(value, position) => change('movie_folder', value, position)}
          onCursor={(position) => {
            cursor.current = { field: 'movie_folder', position }
          }}
        />
        <PatternInput
          label={t('settings.files.naming.movieFile')}
          value={patterns.movie_file}
          onChange={(value, position) => change('movie_file', value, position)}
          onCursor={(position) => {
            cursor.current = { field: 'movie_file', position }
          }}
        />

        <TokenList tokens={tokens} onInsert={insert} />

        <Switch
          label={t('settings.files.naming.umlauts')}
          hint={t('settings.files.naming.umlautsHint')}
          checked={patterns.umlauts === 'replace'}
          onChange={(on) => setDraft({ ...patterns, umlauts: on ? 'replace' : 'keep' })}
        />

        <PreviewBox preview={preview} empty={empty} emptyId={emptyId} />

        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => void save()} loading={busy} disabled={!changed || empty} aria-describedby={empty ? emptyId : undefined}>
            {t('common.actions.save')}
          </Button>
          <Button
            variant="ghost"
            onClick={() => setDraft({ ...patterns, movie_folder: saved.defaults.movie_folder, movie_file: saved.defaults.movie_file })}
            disabled={isDefault}
          >
            {t('settings.files.naming.reset')}
          </Button>
        </div>
        <p className="text-xs text-mist-500">{t('settings.files.naming.notes')}</p>
      </Section>
      {Array.isArray(saved.versions) && <VersionNamingSection versions={saved.versions} umlauts={saved.umlauts} tokens={tokens} onChange={versionChanged} />}
    </>
  )
}
