import { useId } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import type { AnswerValue, LanguageEntry, LanguagesQuestion, MediaKind, NumberQuestion, Question } from '../../api/types'
import { Segmented } from '../../components/Segmented'
import { FormMessage } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { formatGbPerHour, languageName, roleButtonText } from './profileText'
import { isLanguageList, readNumberDraft, setLanguageRole, toggleLanguage, type NumberDraft } from './questions'

type Texts = {
  title: string
  hint: string | null
  explain: string | null
  text: (part: string, values?: Record<string, string | number>) => string | null
  option: (value: string) => { title: string; hint: string | null }
}

/**
 * Die Texte einer Frage aus `profiles.questions.<id>`. Eine Art mit eigenen Worten hat sie unter
 * `profiles.seriesQuestions.<id>`; nur was dort steht, gilt statt des Textes der Filme (S2.7). Musik hat alle
 * ihre Texte unter `profiles.musicQuestions.<id>`. Kennt die
 * Oberflaeche eine Frage oder Option nicht, steht ihre Kennung da, statt eines rohen Schluessels.
 */
function useQuestionTexts(id: string, kind: MediaKind = 'movie'): Texts {
  const { t, i18n } = useTranslation()
  const text = (part: string, values?: Record<string, string | number>): string | null => {
    // Musik teilt Kennungen mit den Filmen ("source", "take_now"), meint aber anderes: nie der Text der Filme.
    if (kind === 'album') {
      const music = `profiles.musicQuestions.${id}.${part}`
      return i18n.exists(music) ? t(music, values ?? {}) : null
    }
    const own = kind === 'series' ? `profiles.seriesQuestions.${id}.${part}` : null
    if (own !== null && i18n.exists(own)) return t(own, values ?? {})
    const key = `profiles.questions.${id}.${part}`
    return i18n.exists(key) ? t(key, values ?? {}) : null
  }
  return {
    title: text('title') ?? id,
    hint: text('hint'),
    explain: text('explain'),
    text,
    option: (value) => ({ title: text(`options.${value}.title`) ?? value, hint: text(`options.${value}.hint`) }),
  }
}

/**
 * Eine Frage aus der Liste des Servers, gezeichnet nach ihrem Typ. Eine neue Frage braucht nur
 * Texte; ein Typ, den die Oberflaeche nicht kennt, sagt es und behaelt die Vorgabe.
 */
export function QuestionField({
  question,
  value,
  draft,
  onChange,
  onDraft,
  kind = 'movie',
}: {
  question: Question
  value: AnswerValue | undefined
  draft: NumberDraft | null
  onChange: (value: AnswerValue) => void
  onDraft: (draft: NumberDraft) => void
  kind?: MediaKind
}) {
  switch (question.type) {
    case 'choice':
      return <ChoiceField id={question.id} kind={kind} options={question.options} value={typeof value === 'string' ? value : question.default} onChange={onChange} />
    case 'boolean': {
      const current = typeof value === 'boolean' ? value : question.default
      return (
        <ChoiceField
          id={question.id}
          kind={kind}
          options={[String(question.default), String(!question.default)]}
          value={String(current)}
          onChange={(next) => onChange(next === 'true')}
        />
      )
    }
    case 'languages':
      return <LanguagesField question={question} kind={kind} value={isLanguageList(value) ? value : []} onChange={onChange} />
    case 'number':
      return <NumberField question={question} kind={kind} draft={draft ?? { none: question.nullable, text: '' }} onDraft={onDraft} />
    default:
      return <UnsupportedField id={(question as { id: string }).id} kind={kind} />
  }
}

function Legend({ texts }: { texts: Texts }) {
  return (
    <>
      <legend className="mb-1 text-base font-semibold text-mist-100">{texts.title}</legend>
      {texts.hint && <p className="text-sm text-mist-500">{texts.hint}</p>}
    </>
  )
}

/** Was die Antwort bewirkt, unter der Auswahl: Erst entscheidet man, dann liest man, was das heisst. */
function Explain({ children }: { children: ReactNode }) {
  return <p className="rounded-r-xl border-l-2 border-accent-500/60 bg-ink-900/70 px-4 py-3 text-xs leading-relaxed text-mist-400">{children}</p>
}

function ChoiceField({ id, kind, options, value, onChange }: { id: string; kind: MediaKind; options: readonly string[]; value: string; onChange: (value: string) => void }) {
  const texts = useQuestionTexts(id, kind)
  const name = useId()
  return (
    <fieldset className="flex min-w-0 flex-col gap-2">
      <Legend texts={texts} />
      {options.map((option) => {
        const text = texts.option(option)
        const checked = value === option
        const titleId = `${name}-${option}-title`
        const hintId = `${name}-${option}-hint`
        return (
          <label
            key={option}
            className={
              'flex cursor-pointer items-start gap-3 rounded-xl border px-3 py-2.5 transition-colors ' +
              (checked ? 'border-accent-500/60 bg-accent-500/10' : 'border-ink-700 bg-ink-900/60 hover:border-ink-600')
            }
          >
            <input
              type="radio"
              name={name}
              value={option}
              checked={checked}
              onChange={() => onChange(option)}
              aria-labelledby={titleId}
              aria-describedby={text.hint ? hintId : undefined}
              className="mt-1 h-4 w-4 shrink-0 accent-accent-500"
            />
            <span className="min-w-0 flex-1">
              <span id={titleId} className="block text-sm font-medium text-mist-100">
                {text.title}
              </span>
              {text.hint && (
                <span id={hintId} className="mt-0.5 block text-xs leading-relaxed text-mist-500">
                  {text.hint}
                </span>
              )}
            </span>
          </label>
        )
      })}
      {texts.explain && <Explain>{texts.explain}</Explain>}
    </fieldset>
  )
}

/**
 * Sprachen zum Anhaken, je Sprache Pflicht oder bevorzugt. Die erste angehakte ist Pflicht, jede
 * weitere bevorzugt; umstellen geht daneben. Ohne Sprache geht es nicht weiter.
 */
function LanguagesField({ question, kind, value, onChange }: { question: LanguagesQuestion; kind: MediaKind; value: LanguageEntry[]; onChange: (value: AnswerValue) => void }) {
  const { t } = useTranslation()
  const texts = useQuestionTexts(question.id, kind)
  const required = value.filter((entry) => entry.role === 'required').length
  return (
    <fieldset className="flex min-w-0 flex-col gap-2">
      <Legend texts={texts} />
      <ul className="flex flex-col gap-2">
        {question.codes.map((code) => {
          const entry = value.find((item) => item.code === code)
          const name = languageName(t, code)
          return (
            <li
              key={code}
              className={
                'flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-2 rounded-xl border px-3 py-2 ' +
                (entry ? 'border-accent-500/60 bg-accent-500/10' : 'border-ink-700 bg-ink-900/60')
              }
            >
              <label className="flex min-w-0 cursor-pointer items-center gap-3 py-1">
                <input
                  type="checkbox"
                  checked={entry !== undefined}
                  onChange={() => onChange(toggleLanguage(question, value, code))}
                  className="h-4 w-4 shrink-0 accent-accent-500"
                />
                <span className="text-sm font-medium text-mist-100">{name}</span>
              </label>
              {entry && (
                <Segmented
                  value={entry.role}
                  options={question.roles}
                  onChange={(role) => onChange(setLanguageRole(value, code, role))}
                  label={(role) => roleButtonText(t, role)}
                  ariaLabel={t('profiles.languagesField.roleLabel', { language: name })}
                />
              )}
            </li>
          )
        })}
      </ul>
      {value.length === 0 ? (
        <FormMessage>{t('profiles.languagesField.atLeastOne')}</FormMessage>
      ) : required === 0 ? (
        <FormMessage tone="info">{t('profiles.languagesField.noneRequired')}</FormMessage>
      ) : null}
      {texts.explain && <Explain>{texts.explain}</Explain>}
    </fieldset>
  )
}

/**
 * Eine Zahl mit "keine Grenze". Getippt wird frei; weiter geht es erst mit einer ganzen Zahl im
 * erlaubten Bereich. Das Beispiel darunter rechnet live mit.
 */
function NumberField({ question, draft, onDraft, kind }: { question: NumberQuestion; draft: NumberDraft; onDraft: (draft: NumberDraft) => void; kind: MediaKind }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const texts = useQuestionTexts(question.id, kind)
  const inputId = useId()
  const noteId = `${inputId}-note`
  const read = readNumberDraft(question, draft)
  const none = draft.none && question.nullable

  let note: string | null
  if (none) note = texts.text('noneText')
  else if (read.ok && read.value !== null) {
    // Ein Film dauert etwa zwei Stunden, eine Folge etwa 45 Minuten. Der Text der Art nimmt sich, was er braucht.
    note = texts.text('example', {
      value: formatGbPerHour(read.value, language),
      double: formatGbPerHour(read.value * 2, language),
      episode: formatGbPerHour(read.value * 0.75, language),
    })
  }
  else note = texts.text('exampleDefault')

  return (
    <fieldset className="flex min-w-0 flex-col gap-3">
      <Legend texts={texts} />
      {question.nullable && (
        <label className="flex w-fit cursor-pointer items-center gap-3 text-sm font-medium text-mist-200">
          <input
            type="checkbox"
            checked={draft.none}
            onChange={(event) => onDraft({ ...draft, none: event.target.checked })}
            className="h-4 w-4 shrink-0 accent-accent-500"
          />
          {texts.text('none') ?? t('profiles.numberField.none')}
        </label>
      )}
      <div className="flex flex-col gap-1.5">
        <label htmlFor={inputId} className={'text-sm font-medium ' + (none ? 'text-mist-600' : 'text-mist-300')}>
          {texts.text('label') ?? texts.title}
        </label>
        <input
          id={inputId}
          type="text"
          inputMode="numeric"
          autoComplete="off"
          maxLength={9}
          value={draft.text}
          disabled={none}
          aria-invalid={!read.ok}
          aria-describedby={note ? noteId : undefined}
          onChange={(event) => onDraft({ ...draft, text: event.target.value })}
          className="w-32 rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 text-mist-100 tabular-nums focus:border-accent-500 focus:outline-none disabled:opacity-50"
        />
      </div>
      {!read.ok && <FormMessage>{t('profiles.numberField.invalid', { min: formatNumber(question.min, language), max: formatNumber(question.max, language) })}</FormMessage>}
      {note && (
        <p id={noteId} className="text-sm text-mist-400">
          {note}
        </p>
      )}
    </fieldset>
  )
}

function UnsupportedField({ id, kind }: { id: string; kind: MediaKind }) {
  const { t } = useTranslation()
  const texts = useQuestionTexts(id, kind)
  return (
    <div className="flex flex-col gap-1">
      <p className="text-base font-semibold text-mist-100">{texts.title}</p>
      <p className="text-sm text-mist-500">{t('profiles.wizard.unsupported')}</p>
    </div>
  )
}
