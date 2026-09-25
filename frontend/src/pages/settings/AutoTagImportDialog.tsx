import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'

import { autoTagsApi, type AutoTagConditionType, type AutoTagKind, type AutoTagRuleNote, type SourceAutoTag } from '../../api/autoTags'
import { errorText } from '../../api/client'
import { sourcesApi } from '../../api/sources'
import type { Source } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { TagChips } from '../../components/TagEditor'
import { Button, FormMessage, SelectField, Toggle } from '../../components/ui'
import { APP_NAME, APP_OF_KIND, conditionLine, conditionName } from './autoTagText'

const CONDITION_TYPES: readonly string[] = ['genre', 'year', 'root_folder', 'runtime', 'keyword', 'studio', 'original_language', 'profile', 'status', 'monitored', 'tag', 'series_type']

/** Woertliche Schluessel, damit keys.test sie findet. */
function problemText(t: TFunction, note: AutoTagRuleNote): string {
  // Bei diesen beiden nennt der Server die Art der Bedingung; gezeigt wird ihr Name.
  const value = (note.code === 'condition_empty' || note.code === 'condition_unsupported') && CONDITION_TYPES.includes(note.value)
    ? conditionName(t, note.value as AutoTagConditionType)
    : note.value
  switch (note.code) {
    case 'no_tags':
      return t('tags.auto.import.problem.no_tags')
    case 'no_conditions':
      return t('tags.auto.import.problem.no_conditions')
    case 'profile_unknown':
      return t('tags.auto.import.problem.profile_unknown', { value })
    case 'root_folder_unknown':
      return t('tags.auto.import.problem.root_folder_unknown', { value })
    case 'language_unknown':
      return t('tags.auto.import.problem.language_unknown', { value })
    case 'status_unknown':
      return t('tags.auto.import.problem.status_unknown', { value })
    case 'series_type_unknown':
      return t('tags.auto.import.problem.series_type_unknown', { value })
    case 'tag_unknown':
      return t('tags.auto.import.problem.tag_unknown')
    case 'range_invalid':
      return t('tags.auto.import.problem.range_invalid', { value })
    case 'condition_empty':
      return t('tags.auto.import.problem.condition_empty', { value })
    case 'condition_unknown':
      return t('tags.auto.import.problem.condition_unknown', { value })
    case 'condition_unsupported':
      return t('tags.auto.import.problem.condition_unsupported', { value })
    default:
      return t('tags.auto.import.problem.other', { value: note.code })
  }
}

function droppedText(t: TFunction, note: AutoTagRuleNote): string {
  if (note.code === 'metadata_profile') return t('tags.auto.import.dropped.metadata_profile')
  return t('tags.auto.import.problem.other', { value: note.code })
}

/**
 * Regeln fuer automatische Tags aus Radarr, Sonarr oder Lidarr holen: lesen, ansehen, die
 * gewaehlten mit derselben Adresse speichern wie eine neue Regel. Was sich nicht abbilden laesst, kommt nicht mit und
 * sagt warum; eine Regel mit vorhandenem Namen bleibt draussen.
 */
export function AutoTagImportDialog({
  kind,
  profiles,
  onClose,
  onDone,
}: {
  kind: AutoTagKind
  profiles: Map<number, string>
  onClose: () => void
  /** `close`: alles ging; sonst bleibt der Dialog mit den Fehlern offen. */
  onDone: (taken: number, changed: number, close: boolean) => void
}) {
  const { t } = useTranslation()
  const app = APP_OF_KIND[kind]
  const appName = APP_NAME[app]
  const [sources, setSources] = useState<Source[] | null>(null)
  const [sourceId, setSourceId] = useState<number | null>(null)
  const [rules, setRules] = useState<SourceAutoTag[] | null>(null)
  const [chosen, setChosen] = useState<Set<number>>(new Set())
  const [reading, setReading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [problems, setProblems] = useState<string[]>([])

  useEffect(() => {
    let current = true
    sourcesApi.list().then(
      (found) => {
        if (!current) return
        const fitting = Array.isArray(found) ? found.filter((source) => source.app === app && source.has_api_key) : []
        setSources(fitting)
        setSourceId(fitting.length > 0 ? fitting[0].id : null)
      },
      (error: unknown) => {
        if (!current) return
        setSources([])
        setProblems([errorText(t, error)])
      },
    )
    return () => {
      current = false
    }
  }, [app, t])

  async function read() {
    if (sourceId === null || reading) return
    setReading(true)
    setProblems([])
    try {
      const found = (await autoTagsApi.fromSource(sourceId)).rules
      setRules(found)
      setChosen(new Set(found.flatMap((rule, index) => (rule.importable ? [index] : []))))
    } catch (error) {
      setRules(null)
      setProblems([errorText(t, error)])
    } finally {
      setReading(false)
    }
  }

  function pick(index: number, on: boolean) {
    setChosen((before) => {
      const next = new Set(before)
      if (on) next.add(index)
      else next.delete(index)
      return next
    })
  }

  async function take() {
    if (rules === null || chosen.size === 0 || saving) return
    setSaving(true)
    setProblems([])
    let changed = 0
    const taken: number[] = []
    const failed: string[] = []
    // Eine nach der anderen: jede laeuft nach dem Speichern ueber die ganze Bibliothek der Art.
    for (const index of [...chosen].sort((a, b) => a - b)) {
      const rule = rules[index]
      try {
        const saved = await autoTagsApi.create({ kind, name: rule.name, tags: rule.tags, remove_automatically: rule.remove_automatically, conditions: rule.conditions })
        taken.push(index)
        changed += saved.changed
      } catch (error) {
        failed.push(t('tags.auto.import.failed', { name: rule.name, reason: errorText(t, error) }))
      }
    }
    setSaving(false)
    if (failed.length === 0) {
      onDone(taken.length, changed, true)
      return
    }
    // Uebernommenes gilt ab jetzt als vorhanden, damit ein zweiter Druck es nicht noch einmal schickt.
    setRules(rules.map((rule, index) => (taken.includes(index) ? { ...rule, importable: false, exists: true } : rule)))
    setChosen((before) => new Set([...before].filter((index) => !taken.includes(index))))
    setProblems(failed)
    if (taken.length > 0) onDone(taken.length, changed, false)
  }

  return (
    <Dialog
      open
      title={t('tags.auto.import.title', { app: appName })}
      onClose={() => !saving && onClose()}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            {t('common.actions.cancel')}
          </Button>
          {rules !== null && rules.length > 0 && (
            <Button onClick={() => void take()} loading={saving} disabled={chosen.size === 0}>
              {t('tags.auto.import.take', { count: chosen.size })}
            </Button>
          )}
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {sources !== null && sources.length === 0 && problems.length === 0 && (
          <p className="text-sm text-mist-400">{t('tags.auto.import.noSource', { app: appName })}</p>
        )}
        {sources !== null && sources.length > 0 && (
          <div className="flex flex-col gap-2">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
              <div className="min-w-0 flex-1">
                <SelectField label={t('tags.auto.import.source')} value={sourceId ?? ''} onChange={(event) => setSourceId(Number(event.target.value))}>
                  {sources.map((source) => (
                    <option key={source.id} value={source.id}>
                      {source.name}
                    </option>
                  ))}
                </SelectField>
              </div>
              <Button variant="ghost" onClick={() => void read()} loading={reading} disabled={saving}>
                {t('tags.auto.import.read')}
              </Button>
            </div>
            <p className="text-xs text-mist-500">{t('tags.auto.import.hint')}</p>
          </div>
        )}
        {problems.map((text) => (
          <FormMessage key={text}>{text}</FormMessage>
        ))}
        {rules !== null && rules.length === 0 && <p className="text-sm text-mist-400">{t('tags.auto.import.none')}</p>}
        {rules !== null && rules.length > 0 && (
          <ul className="flex flex-col divide-y divide-ink-700 rounded-xl border border-ink-700" aria-label={t('tags.auto.import.title', { app: appName })}>
            {rules.map((rule, index) => (
              <li key={`${index}-${rule.name}`} className="flex flex-col gap-2 px-4 py-3">
                <Toggle
                  label={rule.name}
                  checked={chosen.has(index)}
                  disabled={!rule.importable || saving}
                  onChange={(on) => pick(index, on)}
                  hint={rule.remove_automatically ? t('tags.auto.import.removes') : undefined}
                />
                <TagChips tags={rule.tags} />
                <ul className="flex flex-col gap-0.5 text-xs text-mist-400">
                  {rule.conditions.map((condition, number) => (
                    <li key={number} className="wrap-anywhere">
                      {conditionLine(t, condition, profiles)}
                    </li>
                  ))}
                </ul>
                {rule.exists && <p className="text-xs text-mist-500">{t('tags.auto.import.exists')}</p>}
                {rule.problems.length > 0 && (
                  <div className="text-xs text-bad-400">
                    <p>{t('tags.auto.import.notTaken')}</p>
                    <ul className="list-inside list-disc">
                      {rule.problems.map((note, number) => (
                        <li key={number} className="wrap-anywhere">
                          {problemText(t, note)}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {rule.dropped.map((note, number) => (
                  <p key={number} className="text-xs text-mist-500">
                    {droppedText(t, note)}
                  </p>
                ))}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Dialog>
  )
}
