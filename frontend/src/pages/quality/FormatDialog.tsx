import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { expertApi } from '../../api/expert'
import type { CustomFormat, FormatCondition, FormatSpecification, FormatTestCondition, FormatTestResult, MediaKind } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Button, Field, FormMessage } from '../../components/ui'
import { conditionName, conditionOf, emptySpecification, fieldOf, incompleteSpecification, withField } from './conditions'

const INPUT = 'rounded-lg border border-ink-700 bg-ink-900 px-2.5 py-1.5 text-sm text-mist-100 focus:border-accent-500 focus:outline-none'

/**
 * Ein Format anlegen oder aendern, mit den Bedingungen darunter: Muster fuer Release-Titel, Gruppe und Ausgabe,
 * Auswahl fuer Quelle, Aufloesung, Sprache und Indexer-Merkmal, ein Bereich fuer Groesse und Jahr.
 *
 * Die Regel dahinter ist Radarrs (`services/releases/formats.py`): Bedingungen derselben Art bilden eine Gruppe,
 * eine Gruppe trifft zu, wenn keine Pflicht-Bedingung darin scheitert und mindestens eine trifft, und das Format
 * trifft, wenn jede Gruppe zutrifft. Deshalb steht der Satz dazu im Dialog.
 *
 * ⚠️ Ein Format aus den Leitfaeden wird beim Speichern zu einem eigenen; die Leitfaeden fassen es dann nicht mehr an.
 *
 * Der Test darunter schickt die Bedingungen, wie sie gerade im Dialog stehen, und einen Release-Namen an den Server;
 * gespeichert wird nichts. Das Ergebnis gehoert zu genau dem Stand, der getestet wurde: aendert sich eine
 * Bedingung, verschwindet es, statt neben dem Geaenderten weiter "zaehlt" zu sagen.
 */
export function FormatDialog({
  kind,
  format,
  conditions,
  onClose,
  onDone,
}: {
  kind: MediaKind
  format: CustomFormat | null
  conditions: FormatCondition[]
  onClose: () => void
  onDone: () => void
}) {
  const { t } = useTranslation()
  const [name, setName] = useState(format?.name ?? '')
  const [specifications, setSpecifications] = useState<FormatSpecification[]>(format?.specifications ?? [])
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const [releaseName, setReleaseName] = useState('')
  const [tested, setTested] = useState<FormatTestResult | null>(null)
  const [stale, setStale] = useState(false)
  const [testing, setTesting] = useState(false)

  const incomplete = specifications.some((specification) => incompleteSpecification(specification, conditionOf(conditions, specification.implementation)))
  const canSave = name.trim() !== '' && !incomplete && !busy

  function forgetTest() {
    if (tested !== null) setStale(true)
    setTested(null)
  }

  function change(index: number, next: FormatSpecification) {
    forgetTest()
    setSpecifications((before) => before.map((entry, position) => (position === index ? next : entry)))
  }

  function add() {
    const first = conditions[0]
    if (first === undefined) return
    forgetTest()
    setSpecifications((before) => [...before, emptySpecification(first)])
  }

  function remove(index: number) {
    forgetTest()
    setSpecifications((before) => before.filter((_, position) => position !== index))
  }

  async function test() {
    if (releaseName.trim() === '' || incomplete || testing) return
    setTesting(true)
    setProblem(null)
    try {
      setTested(await expertApi.testFormat(kind, releaseName.trim(), specifications))
      setStale(false)
    } catch (error: unknown) {
      setProblem(error)
    } finally {
      setTesting(false)
    }
  }

  async function save() {
    if (!canSave) return
    setBusy(true)
    setProblem(null)
    try {
      if (format === null) await expertApi.addFormat(kind, name.trim(), specifications)
      else await expertApi.changeFormat(format.id, { name: name.trim(), specifications })
      onDone()
    } catch (error: unknown) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open
      wide
      title={format === null ? t('quality.formats.addTitle') : t('quality.formats.changeTitle', { name: format.name })}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('quality.formats.cancel')}
          </Button>
          <Button onClick={() => void save()} disabled={!canSave} loading={busy}>
            {t('quality.formats.save')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {format?.origin === 'trash' && <FormMessage tone="info">{t('quality.formats.becomesOwn')}</FormMessage>}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}

        <Field label={t('quality.formats.name')} value={name} maxLength={200} onChange={(event) => setName(event.target.value)} />

        <div className="flex flex-col gap-2">
          <p className="text-sm font-medium text-mist-300">{t('quality.formats.conditionsTitle')}</p>
          <p className="text-xs text-mist-500">{t('quality.formats.conditionsRule')}</p>
          {specifications.length === 0 && <p className="text-sm text-mist-500">{t('quality.formats.noConditions')}</p>}
          {specifications.map((specification, index) => (
            <ConditionRow
              key={index}
              specification={specification}
              conditions={conditions}
              tested={tested?.conditions.find((entry) => entry.index === index) ?? null}
              onChange={(next) => change(index, next)}
              onRemove={() => remove(index)}
            />
          ))}
          <div>
            <Button size="sm" variant="ghost" onClick={add}>
              <Symbol name="plus" className="h-3.5 w-3.5" />
              {t('quality.formats.addCondition')}
            </Button>
          </div>
        </div>

        <div className="flex flex-col gap-2 border-t border-ink-700 pt-4">
          <p className="text-sm font-medium text-mist-300">{t('quality.formats.testTitle')}</p>
          <p className="text-xs text-mist-500">{t('quality.formats.testIntro')}</p>
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={releaseName}
              maxLength={500}
              onChange={(event) => {
                forgetTest()
                setReleaseName(event.target.value)
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter') void test()
              }}
              aria-label={t('quality.formats.testName')}
              placeholder={t('quality.formats.testName')}
              className={INPUT + ' min-w-0 flex-1 font-mono text-xs'}
            />
            <Button size="sm" variant="ghost" onClick={() => void test()} disabled={releaseName.trim() === '' || incomplete} loading={testing}>
              {t('quality.formats.testRun')}
            </Button>
          </div>
          {stale && tested === null && <p className="text-xs text-mist-500">{t('quality.formats.testStale')}</p>}
          {tested !== null && (
            <div role="status" className="flex flex-col gap-1 text-sm">
              <p className={tested.matches ? 'font-medium text-emerald-300' : 'font-medium text-rose-300'}>
                {tested.matches ? t('quality.formats.testMatches') : t('quality.formats.testMisses')}
              </p>
              <p className="text-xs text-mist-500">
                {t('quality.formats.testRead', {
                  quality: tested.parsed.quality ?? t('quality.formats.testUnknown'),
                  group: tested.parsed.group ?? t('quality.formats.testUnknown'),
                  languages: (tested.parsed.languages ?? []).join(', ') || t('quality.formats.testUnknown'),
                })}
              </p>
            </div>
          )}
        </div>
      </div>
    </Dialog>
  )
}

function ConditionRow({
  specification,
  conditions,
  tested,
  onChange,
  onRemove,
}: {
  specification: FormatSpecification
  conditions: FormatCondition[]
  tested: FormatTestCondition | null
  onChange: (next: FormatSpecification) => void
  onRemove: () => void
}) {
  const { t } = useTranslation()
  const condition = conditionOf(conditions, specification.implementation)
  const label = conditionName(t, specification.implementation)

  function pick(implementation: string) {
    const next = conditionOf(conditions, implementation)
    if (next !== null) onChange({ ...emptySpecification(next), negate: specification.negate, required: specification.required })
  }

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-ink-700 bg-ink-900/60 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <select value={specification.implementation} onChange={(event) => pick(event.target.value)} aria-label={t('quality.formats.conditionKind')} className={INPUT}>
          {conditions.map((entry) => (
            <option key={entry.implementation} value={entry.implementation}>
              {conditionName(t, entry.implementation)}
            </option>
          ))}
          {condition === null && <option value={specification.implementation}>{label}</option>}
        </select>

        {condition?.value === 'regex' && (
          <input
            value={String(fieldOf(specification, 'value') ?? '')}
            onChange={(event) => onChange(withField(specification, 'value', event.target.value))}
            aria-label={t('quality.formats.pattern', { condition: label })}
            placeholder={t('quality.formats.patternHint')}
            className={INPUT + ' min-w-0 flex-1 font-mono text-xs'}
          />
        )}

        {condition?.value === 'choice' && (
          <select
            value={String(fieldOf(specification, 'value') ?? '')}
            onChange={(event) => onChange(withField(specification, 'value', Number(event.target.value)))}
            aria-label={t('quality.formats.value', { condition: label })}
            className={INPUT}
          >
            {condition.options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        )}

        {condition?.value === 'range' && (
          <span className="flex items-center gap-1.5 text-xs text-mist-500">
            <input
              type="number"
              value={String(fieldOf(specification, 'min') ?? 0)}
              onChange={(event) => onChange(withField(specification, 'min', Number(event.target.value)))}
              aria-label={t('quality.formats.from', { condition: label })}
              className={INPUT + ' w-24'}
            />
            <input
              type="number"
              value={String(fieldOf(specification, 'max') ?? 0)}
              onChange={(event) => onChange(withField(specification, 'max', Number(event.target.value)))}
              aria-label={t('quality.formats.to', { condition: label })}
              className={INPUT + ' w-24'}
            />
            {condition.unit === 'gb' ? t('quality.formats.unitGb') : t('quality.formats.unitYear')}
          </span>
        )}

        <Button size="sm" variant="ghost" onClick={onRemove} aria-label={t('quality.formats.removeCondition', { condition: label })}>
          <Symbol name="trash" className="h-3.5 w-3.5" />
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-4 text-xs text-mist-400">
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={specification.negate === true} onChange={(event) => onChange({ ...specification, negate: event.target.checked })} className="accent-accent-500" />
          {t('quality.formats.negate')}
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={specification.required === true} onChange={(event) => onChange({ ...specification, required: event.target.checked })} className="accent-accent-500" />
          {t('quality.formats.required')}
        </label>
        {condition?.except_language === true && (
          <label className="flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={fieldOf(specification, 'exceptLanguage') === true}
              onChange={(event) => onChange(withField(specification, 'exceptLanguage', event.target.checked))}
              className="accent-accent-500"
            />
            {t('quality.formats.exceptLanguage')}
          </label>
        )}
        {tested !== null && (
          <span className={'ml-auto rounded-full px-2 py-0.5 ' + (tested.problem !== null ? 'bg-amber-500/15 text-amber-300' : tested.result ? 'bg-emerald-500/15 text-emerald-300' : 'bg-rose-500/15 text-rose-300')}>
            {tested.problem !== null ? t('quality.formats.testBroken') : tested.result ? t('quality.formats.testHit') : t('quality.formats.testMiss')}
          </span>
        )}
      </div>
    </div>
  )
}
