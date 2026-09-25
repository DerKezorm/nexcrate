import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { autoTagsApi, type AutoTag, type AutoTagCondition, type AutoTagConditionType, type AutoTagKind, type AutoTagOptions } from '../../api/autoTags'
import { errorText } from '../../api/client'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Button, Field, FormMessage, SelectField, Toggle } from '../../components/ui'
import { conditionName, seriesTypeName, statusName } from './autoTagText'

/** Eine Bedingung im Entwurf: was getippt ist, als Text. */
type Draft = { type: AutoTagConditionType; negate: boolean; required: boolean; text: string; min: string; max: string; profile: string }

const LIST_TYPES: readonly AutoTagConditionType[] = ['genre', 'keyword', 'studio']
const RANGE_TYPES: readonly AutoTagConditionType[] = ['year', 'runtime']

function draftOf(condition: AutoTagCondition): Draft {
  return {
    type: condition.type,
    negate: condition.negate,
    required: condition.required,
    text: condition.values ? condition.values.join(', ') : (condition.value ?? ''),
    min: condition.min === null || condition.min === undefined ? '' : String(condition.min),
    max: condition.max === null || condition.max === undefined ? '' : String(condition.max),
    profile: condition.profile_id ? String(condition.profile_id) : '',
  }
}

function splitList(text: string): string[] {
  return text
    .split(',')
    .map((part) => part.trim())
    .filter((part) => part !== '')
}

/** Was hinausgeht; null, wenn eine Zahl keine ist (der Server prueft den Rest). */
function conditionOf(draft: Draft): AutoTagCondition | null {
  const base = { type: draft.type, negate: draft.negate, required: draft.required }
  if (LIST_TYPES.includes(draft.type)) return { ...base, values: splitList(draft.text) }
  if (RANGE_TYPES.includes(draft.type)) {
    const min = Number(draft.min.trim())
    const max = Number(draft.max.trim())
    if (draft.min.trim() === '' || draft.max.trim() === '' || !Number.isInteger(min) || !Number.isInteger(max)) return null
    return { ...base, min, max }
  }
  if (draft.type === 'profile') return { ...base, profile_id: Number(draft.profile) || null }
  if (draft.type === 'monitored') return base
  return { ...base, value: draft.text.trim() }
}

/** Der erste sinnvolle Wert fuer eine Auswahl, damit eine neue Bedingung sofort gueltig sein kann. */
function firstValue(type: AutoTagConditionType, options: AutoTagOptions): Pick<Draft, 'text' | 'profile'> {
  if (type === 'status') return { text: options.statuses[0] ?? '', profile: '' }
  if (type === 'series_type') return { text: options.series_types[0] ?? '', profile: '' }
  if (type === 'profile') return { text: '', profile: options.profiles[0] ? String(options.profiles[0].id) : '' }
  return { text: '', profile: '' }
}

/**
 * Eine Regel, die Tags von selbst vergibt, wie Auto Tagging in Radarr, Sonarr und Lidarr:
 * Name, Tags, "Tags automatisch entfernen" und Bedingungen. Bedingungen gleicher Art reichen einzeln, verschiedene
 * Arten muessen alle passen; "Pflicht" muss passen, "Umkehren" dreht eine um. Beim Speichern laeuft jede Regel der Art
 * sofort ueber die Bibliothek.
 */
export function AutoTagDialog({
  kind,
  rule,
  options,
  onClose,
  onSaved,
}: {
  kind: AutoTagKind
  rule: AutoTag | null
  options: AutoTagOptions
  onClose: () => void
  onSaved: (changed: number) => void
}) {
  const { t } = useTranslation()
  const listId = useId()
  const [name, setName] = useState(rule?.name ?? '')
  const [tags, setTags] = useState((rule?.tags ?? []).join(', '))
  const [remove, setRemove] = useState(rule?.remove_automatically ?? false)
  const [drafts, setDrafts] = useState<Draft[]>(() => (rule?.conditions ?? []).map(draftOf))
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  const set = (index: number, values: Partial<Draft>) => setDrafts((current) => current.map((draft, at) => (at === index ? { ...draft, ...values } : draft)))
  const add = () => {
    const type = options.conditions[0] ?? 'genre'
    setDrafts((current) => [...current, { type, negate: false, required: false, min: '', max: '', ...firstValue(type, options) }])
  }

  async function save() {
    if (busy) return
    const conditions = drafts.map(conditionOf)
    if (name.trim() === '' || splitList(tags).length === 0 || conditions.length === 0) {
      setProblem(t('tags.auto.problem.missing'))
      return
    }
    if (conditions.some((condition) => condition === null)) {
      setProblem(t('tags.auto.problem.range'))
      return
    }
    setBusy(true)
    setProblem(null)
    const body = { kind, name: name.trim(), tags: splitList(tags), remove_automatically: remove, conditions: conditions as AutoTagCondition[] }
    try {
      const saved = rule === null ? await autoTagsApi.create(body) : await autoTagsApi.update(rule.id, body)
      onSaved(saved.changed)
    } catch (error) {
      setProblem(errorText(t, error))
      setBusy(false)
    }
  }

  function suggestions(type: AutoTagConditionType): string[] {
    if (type === 'genre') return options.genres
    if (type === 'root_folder') return options.root_folders
    if (type === 'original_language') return options.languages
    return []
  }

  return (
    <Dialog
      open
      title={rule === null ? t('tags.auto.dialogNew') : t('tags.auto.dialogEdit', { name: rule.name })}
      onClose={() => !busy && onClose()}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void save()} loading={busy}>
            {t('common.actions.save')}
          </Button>
        </>
      }
    >
      <form
        noValidate
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault()
          void save()
        }}
      >
        <Field label={t('tags.auto.name')} value={name} onChange={(event) => setName(event.target.value)} maxLength={100} autoComplete="off" />
        <Field label={t('tags.auto.tags')} hint={t('tags.addHint')} value={tags} onChange={(event) => setTags(event.target.value)} autoComplete="off" />
        <Toggle label={t('tags.auto.remove')} hint={t('tags.auto.removeHint')} checked={remove} onChange={setRemove} />

        <fieldset className="flex flex-col gap-3">
          <legend className="mb-1 text-sm font-medium text-mist-300">{t('tags.auto.conditions')}</legend>
          <p className="text-xs text-mist-500">{t('tags.auto.conditionsHint')}</p>
          {drafts.length > 0 && (
            <ol className="flex flex-col gap-3">
              {drafts.map((draft, index) => {
                const offered = suggestions(draft.type)
                return (
                  <li key={index} aria-label={t('tags.auto.condition', { number: index + 1 })} className="flex flex-col gap-3 rounded-xl border border-ink-700 bg-ink-850 p-3">
                    <SelectField
                      label={t('tags.auto.type.label')}
                      value={draft.type}
                      onChange={(event) => {
                        const type = event.target.value as AutoTagConditionType
                        set(index, { type, min: '', max: '', ...firstValue(type, options) })
                      }}
                    >
                      {options.conditions.map((type) => (
                        <option key={type} value={type}>
                          {conditionName(t, type)}
                        </option>
                      ))}
                    </SelectField>
                    {RANGE_TYPES.includes(draft.type) ? (
                      <div className="grid gap-3 sm:grid-cols-2">
                        <Field label={t('tags.auto.from')} value={draft.min} onChange={(event) => set(index, { min: event.target.value })} inputMode="numeric" autoComplete="off" />
                        <Field label={t('tags.auto.to')} value={draft.max} onChange={(event) => set(index, { max: event.target.value })} inputMode="numeric" autoComplete="off" />
                      </div>
                    ) : draft.type === 'status' || draft.type === 'series_type' ? (
                      <SelectField label={t('tags.auto.value')} value={draft.text} onChange={(event) => set(index, { text: event.target.value })}>
                        {(draft.type === 'status' ? options.statuses : options.series_types).map((value) => (
                          <option key={value} value={value}>
                            {draft.type === 'status' ? statusName(t, value) : seriesTypeName(t, value)}
                          </option>
                        ))}
                      </SelectField>
                    ) : draft.type === 'profile' ? (
                      <SelectField label={t('tags.auto.value')} value={draft.profile} onChange={(event) => set(index, { profile: event.target.value })}>
                        {options.profiles.map((profile) => (
                          <option key={profile.id} value={String(profile.id)}>
                            {profile.name}
                          </option>
                        ))}
                      </SelectField>
                    ) : draft.type === 'monitored' ? null : (
                      <>
                        <Field
                          label={t('tags.auto.value')}
                          hint={LIST_TYPES.includes(draft.type) ? t('tags.auto.listHint') : undefined}
                          value={draft.text}
                          onChange={(event) => set(index, { text: event.target.value })}
                          list={offered.length > 0 ? `${listId}-${index}` : undefined}
                          autoComplete="off"
                        />
                        {offered.length > 0 && (
                          <datalist id={`${listId}-${index}`}>
                            {offered.map((value) => (
                              <option key={value} value={value} />
                            ))}
                          </datalist>
                        )}
                      </>
                    )}
                    <div className="flex flex-wrap items-center gap-4">
                      <Toggle label={t('tags.auto.negate')} checked={draft.negate} onChange={(checked) => set(index, { negate: checked })} />
                      <Toggle label={t('tags.auto.required')} checked={draft.required} onChange={(checked) => set(index, { required: checked })} />
                      <Button size="sm" variant="ghost" onClick={() => setDrafts((current) => current.filter((_draft, at) => at !== index))} aria-label={t('tags.auto.removeCondition', { number: index + 1 })}>
                        <Symbol name="trash" className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </li>
                )
              })}
            </ol>
          )}
          <div>
            <Button size="sm" variant="ghost" onClick={add}>
              <Symbol name="plus" className="h-3.5 w-3.5" />
              {t('tags.auto.addCondition')}
            </Button>
          </div>
        </fieldset>
        {problem !== null && <FormMessage>{problem}</FormMessage>}
      </form>
    </Dialog>
  )
}
