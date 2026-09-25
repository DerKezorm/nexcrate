import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'

import type { DelayRule } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Button, Field, SelectField } from '../../components/ui'
import type { TaggedDraft } from './taggedDelay'

/** Protokoll bevorzugt oder allein, in einer Auswahl statt zwei Schaltern und einem Knopf. */
type Choice = 'usenet' | 'torrent' | 'onlyUsenet' | 'onlyTorrent'
const CHOICES: readonly Choice[] = ['usenet', 'torrent', 'onlyUsenet', 'onlyTorrent']

function choiceOf(rule: DelayRule): Choice {
  if (!rule.enable_torrent) return 'onlyUsenet'
  if (!rule.enable_usenet) return 'onlyTorrent'
  return rule.preferred_protocol
}

/** Woertliche Schluessel, damit keys.test sie findet. */
function choiceText(t: TFunction, choice: Choice): string {
  if (choice === 'usenet') return t('settings.delay.tagged.choice.usenet')
  if (choice === 'torrent') return t('settings.delay.tagged.choice.torrent')
  if (choice === 'onlyUsenet') return t('settings.delay.tagged.choice.onlyUsenet')
  return t('settings.delay.tagged.choice.onlyTorrent')
}

function ruleOf(rule: DelayRule, choice: Choice): DelayRule {
  if (choice === 'onlyUsenet') return { ...rule, enable_usenet: true, enable_torrent: false, preferred_protocol: 'usenet' }
  if (choice === 'onlyTorrent') return { ...rule, enable_usenet: false, enable_torrent: true, preferred_protocol: 'torrent' }
  return { ...rule, enable_usenet: true, enable_torrent: true, preferred_protocol: choice }
}

/**
 * Die Delay-Profile mit Tags aus Radarr, Sonarr und Lidarr: Regeln fuer Titel mit einem der
 * Tags, in ihrer Reihenfolge. Die erste, deren Tag ein Titel traegt, gilt; ohne Treffer gilt die Regel oben. Die
 * Ausnahmen (beste Qualitaet, Punkte) uebernimmt eine neue Regel von oben; aus einer App eingelesen behaelt sie ihre.
 */
export function TaggedDelayRules({
  drafts,
  onChange,
  base,
  disabled,
}: {
  drafts: TaggedDraft[]
  onChange: (next: TaggedDraft[]) => void
  base: DelayRule
  disabled: boolean
}) {
  const { t } = useTranslation()

  const set = (index: number, values: Partial<TaggedDraft>) => onChange(drafts.map((draft, at) => (at === index ? { ...draft, ...values } : draft)))
  const move = (index: number, by: number) => {
    const next = [...drafts]
    const [moved] = next.splice(index, 1)
    next.splice(index + by, 0, moved)
    onChange(next)
  }
  const add = () => onChange([...drafts, { rule: base, tags: '', usenet: String(base.usenet_minutes), torrent: String(base.torrent_minutes) }])

  return (
    <fieldset className="flex flex-col gap-3">
      <legend className="mb-1 text-sm font-medium text-mist-300">{t('settings.delay.tagged.title')}</legend>
      <p className="text-xs text-mist-500">{t('settings.delay.tagged.hint')}</p>
      {drafts.length > 0 && (
        <ol className="flex flex-col gap-3">
          {drafts.map((draft, index) => (
            <li key={index} className="flex flex-col gap-3 rounded-xl border border-ink-700 bg-ink-850 p-3" aria-label={t('settings.delay.tagged.rule', { number: index + 1 })}>
              <Field
                label={t('settings.delay.tagged.tags')}
                hint={t('settings.delay.tagged.tagsHint')}
                value={draft.tags}
                onChange={(event) => set(index, { tags: event.target.value })}
                autoComplete="off"
                disabled={disabled}
              />
              <div className="grid gap-3 sm:grid-cols-3">
                <SelectField
                  label={t('settings.delay.tagged.protocol')}
                  value={choiceOf(draft.rule)}
                  onChange={(event) => set(index, { rule: ruleOf(draft.rule, event.target.value as Choice) })}
                  disabled={disabled}
                >
                  {CHOICES.map((choice) => (
                    <option key={choice} value={choice}>
                      {choiceText(t, choice)}
                    </option>
                  ))}
                </SelectField>
                <Field
                  label={t('settings.delay.waitUsenet')}
                  value={draft.usenet}
                  onChange={(event) => set(index, { usenet: event.target.value })}
                  inputMode="numeric"
                  autoComplete="off"
                  disabled={disabled || !draft.rule.enable_usenet}
                />
                <Field
                  label={t('settings.delay.waitTorrent')}
                  value={draft.torrent}
                  onChange={(event) => set(index, { torrent: event.target.value })}
                  inputMode="numeric"
                  autoComplete="off"
                  disabled={disabled || !draft.rule.enable_torrent}
                />
              </div>
              <div className="flex flex-wrap gap-1.5">
                <Button size="sm" variant="ghost" onClick={() => move(index, -1)} disabled={disabled || index === 0} aria-label={t('settings.delay.tagged.up', { number: index + 1 })}>
                  <Symbol name="arrowUp" className="h-3.5 w-3.5" />
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => move(index, 1)}
                  disabled={disabled || index === drafts.length - 1}
                  aria-label={t('settings.delay.tagged.down', { number: index + 1 })}
                >
                  <Symbol name="arrowUp" className="h-3.5 w-3.5 rotate-180" />
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => onChange(drafts.filter((_draft, at) => at !== index))}
                  disabled={disabled}
                  aria-label={t('settings.delay.tagged.remove', { number: index + 1 })}
                >
                  <Symbol name="trash" className="h-3.5 w-3.5" />
                  {t('settings.delay.tagged.removeButton')}
                </Button>
              </div>
            </li>
          ))}
        </ol>
      )}
      <div>
        <Button size="sm" variant="ghost" onClick={add} disabled={disabled}>
          <Symbol name="plus" className="h-3.5 w-3.5" />
          {t('settings.delay.tagged.add')}
        </Button>
      </div>
    </fieldset>
  )
}
