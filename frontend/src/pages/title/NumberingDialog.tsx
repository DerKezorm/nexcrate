import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { ALIAS_LENGTH_MAX, ALIASES_MAX, numberingApi } from '../../api/numbering'
import type { EpisodeGroup, SeriesNumbering, SeriesNumberingUpdate, TitleDetail } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Button, Field, FormMessage, SelectField, Spinner, Toggle } from '../../components/ui'
import { formatDateTime, formatNumber } from '../../lib/format'

/** Der Wert der Auswahl fuer "keine Gruppe". Eine Kennung von TMDB ist nie leer. */
const NO_GROUP = ''

/** Zustaende von TheXEM, die heissen, dass die gespeicherten Szene-Nummern alt sein koennen. */
const XEM_FAILURES = new Set(['xem_timeout', 'xem_certificate', 'xem_blocked', 'xem_unreachable'])

function groupTypeText(t: TFunction, type: number | null): string {
  switch (type) {
    case 1:
      return t('series.numberingDialog.groupType.1')
    case 2:
      return t('series.numberingDialog.groupType.2')
    case 3:
      return t('series.numberingDialog.groupType.3')
    case 4:
      return t('series.numberingDialog.groupType.4')
    case 5:
      return t('series.numberingDialog.groupType.5')
    case 6:
      return t('series.numberingDialog.groupType.6')
    case 7:
      return t('series.numberingDialog.groupType.7')
    default:
      return t('series.numberingDialog.groupType.unknown')
  }
}

function groupText(t: TFunction, group: EpisodeGroup, language: string): string {
  const type = groupTypeText(t, group.type)
  if (group.episode_count === null) return t('series.numberingDialog.groupOptionNoCount', { name: group.name, type })
  const count = t('series.numberingDialog.groupEpisodes', { count: group.episode_count, value: formatNumber(group.episode_count, language) })
  return t('series.numberingDialog.groupOption', { name: group.name, type, count })
}

function xemStateText(t: TFunction, state: string | null): string {
  switch (state) {
    case null:
      return t('series.numberingDialog.xemState.none')
    case 'mapped':
      return t('series.numberingDialog.xemState.mapped')
    case 'not_listed':
      return t('series.numberingDialog.xemState.not_listed')
    case 'bridge_failed':
      return t('series.numberingDialog.xemState.bridge_failed')
    case 'no_tvdb':
      return t('series.numberingDialog.xemState.no_tvdb')
    case 'xem_timeout':
      return t('series.numberingDialog.xemState.xem_timeout')
    case 'xem_certificate':
      return t('series.numberingDialog.xemState.xem_certificate')
    case 'xem_blocked':
      return t('series.numberingDialog.xemState.xem_blocked')
    case 'xem_unreachable':
      return t('series.numberingDialog.xemState.xem_unreachable')
    default:
      return t('series.numberingDialog.xemState.unknown', { code: state })
  }
}

/**
 * "Nummerierung" einer Serie (S3, Entscheidungen 11, 12 und 15): welche Episodengruppe von TMDB Releases folgen, unter
 * welchen weiteren Titeln sie erscheinen, und was TheXEM ueber die Serie weiss. Die Korrekturen je Folge stehen hier
 * nur als Zahl; geaendert werden sie an der Folge. Gespeichert wird auf Knopfdruck, die Episodengruppe nur, wenn sie
 * sich geaendert hat, denn dann holt der Server sie bei TMDB.
 */
export function NumberingDialog({ title, onClose, onSaved }: { title: TitleDetail; onClose: () => void; onSaved: (numbering: SeriesNumbering) => void }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const [loaded, setLoaded] = useState<SeriesNumbering | null>(null)
  const [loadProblem, setLoadProblem] = useState<unknown>(null)
  const [group, setGroup] = useState(NO_GROUP)
  const [aliases, setAliases] = useState<string[]>([])
  const [scene, setScene] = useState(true)
  const [draft, setDraft] = useState('')
  const [aliasProblem, setAliasProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  useEffect(() => {
    const abort = new AbortController()
    numberingApi.get(title.id, abort.signal).then(
      (result) => {
        if (abort.signal.aborted) return
        setLoaded(result)
        setGroup(result.episode_group_id ?? NO_GROUP)
        setAliases(result.aliases)
        setScene(result.use_scene_numbering)
      },
      (error: unknown) => {
        if (!abort.signal.aborted) setLoadProblem(error)
      },
    )
    return () => abort.abort()
  }, [title.id])

  function addAlias(event?: FormEvent) {
    event?.preventDefault()
    const text = draft.trim()
    if (text === '') return
    if (text.length > ALIAS_LENGTH_MAX) return setAliasProblem(t('series.numberingDialog.aliasTooLong'))
    if (aliases.some((alias) => alias.toLocaleLowerCase() === text.toLocaleLowerCase())) return setAliasProblem(t('series.numberingDialog.aliasExists'))
    if (aliases.length >= ALIASES_MAX) return setAliasProblem(t('series.numberingDialog.aliasesFull'))
    setAliases((current) => [...current, text])
    setDraft('')
    setAliasProblem(null)
  }

  async function save() {
    if (busy || loaded === null) return
    // Ein getippter, aber nicht hinzugefuegter Suchtitel zaehlt mit, sofern er gueltig ist.
    const pending = draft.trim()
    const fresh = pending !== '' && pending.length <= ALIAS_LENGTH_MAX && aliases.length < ALIASES_MAX && !aliases.some((alias) => alias.toLocaleLowerCase() === pending.toLocaleLowerCase())
    const body: SeriesNumberingUpdate = { aliases: fresh ? [...aliases, pending] : aliases }
    const chosen = group === NO_GROUP ? null : group
    if (chosen !== loaded.episode_group_id) body.episode_group_id = chosen
    // B6: der Schalter reist nur mit, wenn er sich geaendert hat.
    if (scene !== loaded.use_scene_numbering) body.use_scene_numbering = scene
    setBusy(true)
    setProblem(null)
    try {
      onSaved(await numberingApi.save(title.id, body))
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  function close() {
    if (!busy) onClose()
  }

  const xemLine =
    loaded === null
      ? null
      : loaded.xem_checked_at
        ? t('series.numberingDialog.xemAt', { state: xemStateText(t, loaded.xem_state), date: formatDateTime(loaded.xem_checked_at, language) })
        : t('series.numberingDialog.xem', { state: xemStateText(t, loaded.xem_state) })

  return (
    <Dialog
      open
      title={t('series.numberingDialog.title')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void save()} loading={busy} disabled={loaded === null}>
            {t('common.actions.save')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-mist-400">{t('series.numberingDialog.intro')}</p>
        {loaded === null ? (
          loadProblem !== null ? (
            <FormMessage>{errorText(t, loadProblem)}</FormMessage>
          ) : (
            <p role="status" className="flex items-center gap-2 text-sm text-mist-400">
              <Spinner />
              {t('series.numberingDialog.loading')}
            </p>
          )
        ) : (
          <>
            {loaded.episode_groups.length === 0 ? (
              <div className="flex flex-col gap-1">
                <p className="text-sm font-medium text-mist-300">{t('series.numberingDialog.groupScheme')}</p>
                <p className="text-sm text-mist-500">{t('series.numberingDialog.noGroups')}</p>
              </div>
            ) : (
              <SelectField
                label={t('series.numberingDialog.groupScheme')}
                hint={t('series.numberingDialog.groupHint')}
                value={group}
                disabled={busy}
                onChange={(event) => setGroup(event.target.value)}
              >
                <option value={NO_GROUP}>{t('series.numberingDialog.groupNone')}</option>
                {loaded.episode_groups.map((item) => (
                  <option key={item.id} value={item.id}>
                    {groupText(t, item, language)}
                  </option>
                ))}
              </SelectField>
            )}

            <fieldset className="flex min-w-0 flex-col gap-2">
              <legend className="mb-1 text-sm font-medium text-mist-300">{t('series.numberingDialog.aliases')}</legend>
              <p className="text-xs text-mist-500">{t('series.numberingDialog.aliasesHint')}</p>
              {loaded.title_en && <p className="text-xs text-mist-500">{t('series.numberingDialog.titleEn', { title: loaded.title_en })}</p>}
              {aliases.length === 0 ? (
                <p className="text-sm text-mist-500">{t('series.numberingDialog.aliasesNone')}</p>
              ) : (
                <ul aria-label={t('series.numberingDialog.aliases')} className="flex flex-col divide-y divide-ink-700/60 rounded-xl border border-ink-700">
                  {aliases.map((alias) => (
                    <li key={alias} className="flex min-w-0 items-center justify-between gap-3 px-3 py-1.5">
                      <span className="min-w-0 text-sm wrap-anywhere text-mist-100">{alias}</span>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busy}
                        aria-label={t('series.numberingDialog.aliasRemove', { alias })}
                        onClick={() => setAliases((current) => current.filter((entry) => entry !== alias))}
                      >
                        <Symbol name="close" className="h-3.5 w-3.5" />
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
              <form onSubmit={addAlias} noValidate className="flex flex-wrap items-end gap-2">
                <div className="min-w-0 flex-1">
                  <Field
                    label={t('series.numberingDialog.aliasNew')}
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    maxLength={ALIAS_LENGTH_MAX + 1}
                    autoComplete="off"
                    className="w-full min-w-0"
                  />
                </div>
                <Button type="submit" variant="ghost" disabled={busy || draft.trim() === ''}>
                  <Symbol name="plus" />
                  {t('series.numberingDialog.aliasAdd')}
                </Button>
              </form>
              {aliasProblem && <FormMessage>{aliasProblem}</FormMessage>}
            </fieldset>

            {loaded.corrections.length > 0 && (
              <p className="text-sm text-mist-300">
                {t('series.numberingDialog.corrections', { count: loaded.corrections.length, value: formatNumber(loaded.corrections.length, language) })}
              </p>
            )}

            {loaded.scene_numbers > 0 && (
              <Toggle
                label={t('series.numberingDialog.sceneNumbering')}
                hint={t('series.numberingDialog.sceneNumberingHint', { count: loaded.scene_numbers, value: formatNumber(loaded.scene_numbers, language) })}
                checked={scene}
                disabled={busy}
                onChange={setScene}
              />
            )}

            <div className="flex flex-col gap-0.5 rounded-xl border border-ink-700 bg-ink-900/60 px-3.5 py-2.5 text-sm">
              <p className="text-mist-200">{xemLine}</p>
              {loaded.xem_state !== null && XEM_FAILURES.has(loaded.xem_state) && <p className="text-xs text-mist-500">{t('series.numberingDialog.xemOld')}</p>}
            </div>
          </>
        )}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}
