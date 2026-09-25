import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { tagsApi, type TagEntry } from '../../api/tags'
import type { FolderRule, FolderRuleWhen, Relocation, Version } from '../../api/types'
import { versionsApi } from '../../api/versions'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { genreText } from '../../lib/names'
import { FolderPicker } from './FolderPicker'

/** TMDBs Genres, englisch wie gespeichert; die Oberflaeche zeigt sie mit `genreText`. */
const GENRES: Record<'movie' | 'series', string[]> = {
  movie: ['Action', 'Adventure', 'Animation', 'Comedy', 'Crime', 'Documentary', 'Drama', 'Family', 'Fantasy', 'History', 'Horror', 'Music', 'Mystery', 'Romance', 'Science Fiction', 'TV Movie', 'Thriller', 'War', 'Western'],
  series: ['Action & Adventure', 'Animation', 'Comedy', 'Crime', 'Documentary', 'Drama', 'Family', 'Kids', 'Mystery', 'News', 'Reality', 'Sci-Fi & Fantasy', 'Soap', 'Talk', 'War & Politics', 'Western'],
}
const WHEN: Record<'movie' | 'series', FolderRuleWhen[]> = {
  movie: ['genre', 'certification', 'tag'],
  series: ['genre', 'tag', 'series_type'],
}
const SERIES_TYPES = ['standard', 'daily', 'anime']

type T = (key: string) => string

/** Woertlich, damit `keys.test.ts` die Schluessel sieht. */
function whenText(t: T, when: FolderRuleWhen): string {
  switch (when) {
    case 'genre':
      return t('settings.files.rules.whenGenre')
    case 'certification':
      return t('settings.files.rules.whenCertification')
    case 'tag':
      return t('settings.files.rules.whenTag')
    case 'series_type':
      return t('settings.files.rules.whenSeriesType')
  }
}

function typeText(t: T, value: string): string {
  if (value === 'anime') return t('settings.files.rules.typeAnime')
  if (value === 'daily') return t('settings.files.rules.typeDaily')
  if (value === 'standard') return t('settings.files.rules.typeStandard')
  return value
}

/**
 * Regeln fuer Mediatheken an einer Fassung: "Wenn Genre, Freigabe, Tag oder Art
 * der Serie, dann dieser Ordner". Die erste passende Regel gewinnt, sonst der Standardordner. Sie gelten nur fuer neue
 * Titel, das sagt der Hinweis. Jede Aenderung speichert die ganze Liste; der Server prueft jeden Ordner wie den der
 * Fassung.
 */
export function FolderRules({ version, kind }: { version: Version; kind: 'movie' | 'series' }) {
  const { t } = useTranslation()
  const notify = useNotice()
  const [rules, setRules] = useState<FolderRule[] | null>(null)
  const [tags, setTags] = useState<TagEntry[]>([])
  const [problem, setProblem] = useState<unknown>(null)
  const [adding, setAdding] = useState(false)
  const [busy, setBusy] = useState(false)
  const [moving, setMoving] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    versionsApi.folderRules(version.id).then(
      (answer) => setRules(answer.rules),
      (error: unknown) => setProblem(error),
    )
    tagsApi.list(controller.signal).then(
      (answer) => setTags(answer.items),
      () => undefined,
    )
    return () => controller.abort()
  }, [version.id])

  async function save(next: FolderRule[]): Promise<boolean> {
    setBusy(true)
    setProblem(null)
    try {
      const answer = await versionsApi.setFolderRules(version.id, next)
      setRules(answer.rules)
      notify(t('settings.files.rules.saved', { label: version.label }))
      return true
    } catch (error) {
      setProblem(error)
      return false
    } finally {
      setBusy(false)
    }
  }

  function valuesText(rule: FolderRule): string {
    if (rule.when === 'genre') return rule.values.map((value) => genreText(t, value)).join(', ')
    if (rule.when === 'tag') return rule.values.map((value) => tags.find((tag) => String(tag.id) === value)?.label ?? value).join(', ')
    if (rule.when === 'series_type') return rule.values.map((value) => typeText(t, value)).join(', ')
    return rule.values.join(', ')
  }

  function move(index: number, by: number) {
    if (rules === null) return
    const next = [...rules]
    const [taken] = next.splice(index, 1)
    next.splice(index + by, 0, taken)
    void save(next)
  }

  if (rules === null) return problem !== null ? <FormMessage>{errorText(t, problem)}</FormMessage> : null

  return (
    <div className="flex min-w-0 flex-col gap-2 border-t border-ink-700/60 pt-3">
      <p className="text-sm font-medium text-mist-200">{t('settings.files.rules.title')}</p>
      <p className="text-xs text-mist-500">{t('settings.files.rules.hint')}</p>
      {rules.length > 0 && (
        <ol aria-label={t('settings.files.rules.listLabel', { label: version.label })} className="flex flex-col gap-1.5">
          {rules.map((rule, index) => (
            <li key={`${rule.when}-${rule.folder}-${index}`} className="flex min-w-0 flex-wrap items-center gap-2 text-sm text-mist-200">
              <span className="min-w-0 wrap-anywhere">
                {t('settings.files.rules.line', { when: whenText(t, rule.when), values: valuesText(rule), folder: rule.folder })}
              </span>
              <span className="ml-auto flex gap-1">
                <Button size="sm" variant="ghost" disabled={busy || index === 0} onClick={() => move(index, -1)} aria-label={t('settings.files.rules.upLabel', { number: index + 1 })}>
                  <Symbol name="chevronDown" className="h-4 w-4 rotate-180" />
                </Button>
                <Button size="sm" variant="ghost" disabled={busy || index === rules.length - 1} onClick={() => move(index, 1)} aria-label={t('settings.files.rules.downLabel', { number: index + 1 })}>
                  <Symbol name="chevronDown" />
                </Button>
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => void save(rules.filter((_rule, position) => position !== index))} aria-label={t('settings.files.rules.removeLabel', { number: index + 1 })}>
                  <Symbol name="close" />
                </Button>
              </span>
            </li>
          ))}
        </ol>
      )}
      {adding ? (
        <RuleForm
          kind={kind}
          tags={tags}
          busy={busy}
          onCancel={() => setAdding(false)}
          onSave={async (rule) => {
            if (await save([...rules, rule])) setAdding(false)
          }}
        />
      ) : (
        <Button size="sm" variant="ghost" className="self-start" disabled={busy} onClick={() => setAdding(true)} aria-label={t('settings.files.rules.addLabel', { label: version.label })}>
          <Symbol name="plus" />
          {t('settings.files.rules.add')}
        </Button>
      )}
      {rules.length > 0 && !adding && (
        <Button size="sm" variant="ghost" className="self-start" disabled={busy} onClick={() => setMoving(true)} aria-label={t('settings.files.rules.moveLabel', { label: version.label })}>
          <Symbol name="swap" />
          {t('settings.files.rules.move')}
        </Button>
      )}
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      {moving && <MoveDialog version={version} onClose={() => setMoving(false)} />}
    </div>
  )
}

function skipText(t: T, skip: string): string {
  switch (skip) {
    case 'folder_missing':
      return t('settings.files.rules.skipMissing')
    case 'other_disk':
      return t('settings.files.rules.skipOtherDisk')
    case 'shared_folder':
      return t('settings.files.rules.skipShared')
    case 'target_exists':
      return t('settings.files.rules.skipTaken')
    case 'busy':
      return t('settings.files.rules.skipBusy')
    default:
      return t('settings.files.rules.skipFailed')
  }
}

/**
 * "Nach Regel umziehen" (entschieden am 25.09.2026): zuerst die Vorschau, welcher vorhandene Titel woanders
 * hingehoerte und warum einer nicht umziehen kann; umgezogen wird erst auf den Knopf, je Titel der ganze Ordner.
 */
function MoveDialog({ version, onClose }: { version: Version; onClose: () => void }) {
  const { t, i18n } = useTranslation()
  const notify = useNotice()
  const [items, setItems] = useState<Relocation[] | null>(null)
  const [problem, setProblem] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    versionsApi.relocations(version.id).then(setItems, (error: unknown) => setProblem(error))
  }, [version.id])

  const movable = (items ?? []).filter((item) => item.skip === null)

  async function move() {
    setBusy(true)
    setProblem(null)
    try {
      const done = await versionsApi.relocate(version.id, movable.map((item) => item.title_id))
      notify(t('settings.files.rules.moved', { count: done.moved.length, value: done.moved.length.toLocaleString(i18n.language), left: done.left.length }))
      onClose()
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  return (
    <Dialog
      open
      wide
      title={t('settings.files.rules.moveTitle', { label: version.label })}
      onClose={() => !busy && onClose()}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void move()} loading={busy} disabled={movable.length === 0}>
            {t('settings.files.rules.moveConfirm', { count: movable.length, value: movable.length.toLocaleString(i18n.language) })}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm text-mist-400">{t('settings.files.rules.moveIntro')}</p>
        {items !== null && items.length === 0 && <p className="text-sm text-mist-300">{t('settings.files.rules.moveNothing')}</p>}
        {items !== null && items.length > 0 && (
          <ul aria-label={t('settings.files.rules.moveList')} className="flex max-h-96 flex-col gap-1.5 overflow-y-auto">
            {items.map((item) => (
              <li key={item.title_id} className="flex min-w-0 flex-col text-sm">
                <span className="text-mist-100">{item.year ? `${item.name} (${item.year})` : item.name}</span>
                <span className="font-mono text-xs break-all text-mist-500">{t('settings.files.rules.moveLine', { from: item.from, to: item.to })}</span>
                {item.skip !== null && <span className="text-xs text-bad-400">{skipText(t, item.skip)}</span>}
              </li>
            ))}
          </ul>
        )}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}

function RuleForm({
  kind,
  tags,
  busy,
  onCancel,
  onSave,
}: {
  kind: 'movie' | 'series'
  tags: TagEntry[]
  busy: boolean
  onCancel: () => void
  onSave: (rule: FolderRule) => Promise<void>
}) {
  const { t } = useTranslation()
  const [when, setWhen] = useState<FolderRuleWhen>(WHEN[kind][0])
  const [values, setValues] = useState<string[]>([])
  const [ratings, setRatings] = useState('')
  const [folder, setFolder] = useState<string | null>(null)
  const [picking, setPicking] = useState(false)

  const choices: { value: string; label: string }[] =
    when === 'genre'
      ? GENRES[kind].map((name) => ({ value: name, label: genreText(t, name) }))
      : when === 'tag'
        ? tags.map((tag) => ({ value: String(tag.id), label: tag.label }))
        : when === 'series_type'
          ? SERIES_TYPES.map((value) => ({ value, label: typeText(t, value) }))
          : []
  const chosen = when === 'certification' ? ratings.split(',').map((value) => value.trim()).filter(Boolean) : values
  const ready = chosen.length > 0 && folder !== null

  function toggle(value: string) {
    setValues(values.includes(value) ? values.filter((item) => item !== value) : [...values, value])
  }

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-ink-700 bg-ink-900/60 p-3">
      <label className="flex flex-col gap-1 text-sm text-mist-300">
        {t('settings.files.rules.whenLabel')}
        <select
          className="rounded-md border border-ink-600 bg-ink-950 px-2 py-1 text-mist-100"
          value={when}
          onChange={(event) => {
            setWhen(event.target.value as FolderRuleWhen)
            setValues([])
          }}
        >
          {WHEN[kind].map((item) => (
            <option key={item} value={item}>
              {whenText(t, item)}
            </option>
          ))}
        </select>
      </label>
      {when === 'certification' ? (
        <label className="flex flex-col gap-1 text-sm text-mist-300">
          {t('settings.files.rules.ratingsLabel')}
          <input className="rounded-md border border-ink-600 bg-ink-950 px-2 py-1 text-mist-100" value={ratings} onChange={(event) => setRatings(event.target.value)} placeholder="0, 6" />
          <span className="text-xs text-mist-500">{t('settings.files.rules.ratingsHint')}</span>
        </label>
      ) : choices.length === 0 ? (
        <p className="text-sm text-mist-500">{t('settings.files.rules.noTags')}</p>
      ) : (
        <fieldset className="flex flex-wrap gap-x-4 gap-y-1">
          <legend className="sr-only">{whenText(t, when)}</legend>
          {choices.map((choice) => (
            <label key={choice.value} className="flex items-center gap-1.5 text-sm text-mist-200">
              <input type="checkbox" checked={values.includes(choice.value)} onChange={() => toggle(choice.value)} />
              {choice.label}
            </label>
          ))}
        </fieldset>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="ghost" onClick={() => setPicking(true)}>
          <Symbol name="folder" />
          {folder === null ? t('settings.files.rules.chooseFolder') : folder}
        </Button>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={!ready || busy} loading={busy} onClick={() => folder !== null && void onSave({ when, values: chosen, folder })}>
          {t('settings.files.rules.save')}
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          {t('common.actions.cancel')}
        </Button>
      </div>
      {picking && (
        <FolderPicker
          title={t('settings.files.rules.pickerTitle')}
          hint={t('settings.files.rules.hint')}
          start={folder}
          onClose={() => setPicking(false)}
          onTake={async (path) => {
            setFolder(path)
            setPicking(false)
          }}
        />
      )}
    </div>
  )
}
