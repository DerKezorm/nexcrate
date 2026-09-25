/**
 * Was der Assistent aus der Fragenliste des Servers ableitet, ohne React: welche Frage gerade
 * gilt, welche Antworten hinausgehen, was eine Sprachliste und eine Zahl erlauben.
 *
 * ⚠️ Der Server bleibt massgeblich. Er setzt Antworten verborgener Fragen selbst auf die Vorgabe
 * zurueck und prueft alles noch einmal. Die Oberflaeche rechnet dasselbe nur, damit sie live
 * zeigen kann, was gefragt wird.
 */

import type { AnswerValue, LanguageEntry, LanguageRole, LanguagesQuestion, NumberQuestion, ProfileAnswers, Question, QuestionCondition, QuestionList } from '../../api/types'

/** Die Frage, die zwischen einfach und ausfuehrlich umschaltet, und ihr ausfuehrlicher Wert. */
export const MODE_QUESTION = 'mode'
export const DETAILED_MODE = 'detailed'

type Answers = Readonly<Record<string, AnswerValue | undefined>>

export function isLanguageList(value: unknown): value is LanguageEntry[] {
  return (
    Array.isArray(value) &&
    value.every((entry) => entry !== null && typeof entry === 'object' && typeof (entry as LanguageEntry).code === 'string' && typeof (entry as LanguageEntry).role === 'string')
  )
}

function sameValue(left: unknown, right: unknown): boolean {
  if (left === right) return true
  if (typeof left === 'object' && left !== null && typeof right === 'object' && right !== null) return JSON.stringify(left) === JSON.stringify(right)
  return false
}

/**
 * Gilt eine einfache Bedingung? `{question, is}`, `{question: "languages", includes}` (jede Rolle)
 * und `{question: "languages", required_at_least}`. null heisst: keine dieser Formen.
 */
function simpleHolds(condition: object, answers: Answers): boolean | null {
  if (!('question' in condition) || typeof condition.question !== 'string') return null
  const value = answers[condition.question]
  if ('is' in condition) return sameValue(value, condition.is)
  if ('includes' in condition) return isLanguageList(value) && value.some((entry) => entry.code === condition.includes)
  if ('required_at_least' in condition && typeof condition.required_at_least === 'number') {
    const least = condition.required_at_least
    return isLanguageList(value) && value.filter((entry) => entry.role === 'required').length >= least
  }
  return null
}

/**
 * Gilt eine Bedingung? Die drei einfachen Formen aus dem Plan und `{any: [[...], ...]}`: Das gilt,
 * wenn jede Bedingung mindestens einer inneren Liste gilt. Innen zaehlen nur die einfachen Formen.
 *
 * Eine unbekannte Form gilt als erfuellt, auch innerhalb von `any` (etwa ein verschachteltes `any`):
 * Dann steht die Frage lieber einmal zu viel da, als still zu verschwinden. Der Server setzt eine
 * verborgene Antwort ohnehin auf die Vorgabe.
 */
export function conditionHolds(condition: QuestionCondition, answers: Answers): boolean {
  // Was vom Server kommt, ist nur behauptet so geformt. Deshalb wird hier wie bei unbekannten Daten geprueft.
  const shape: unknown = condition
  if (shape === null || typeof shape !== 'object') return true
  if (!('any' in shape)) return simpleHolds(shape, answers) ?? true
  const lists: unknown = shape.any
  if (!Array.isArray(lists) || !lists.every((inner) => Array.isArray(inner))) return true
  return (lists as unknown[][]).some((inner) => inner.every((entry) => entry === null || typeof entry !== 'object' || (simpleHolds(entry, answers) ?? true)))
}

/** Steht die Frage gerade da? Ausfuehrliche nur im ausfuehrlichen Modus, und jede Bedingung muss gelten. */
export function isShown(question: Question, answers: Answers): boolean {
  if (question.detailed && answers[MODE_QUESTION] !== DETAILED_MODE) return false
  return (question.when ?? []).every((condition) => conditionHolds(condition, answers))
}

/** Die Vorgabe einer Frage, als eigene Kopie, damit niemand die Liste des Servers veraendert. */
export function defaultValue(question: Question): AnswerValue {
  if (question.type === 'languages') return question.default.map((entry) => ({ ...entry }))
  return question.default
}

/**
 * Die Fragen, die gerade gelten, wie der Server sie rechnet: in der Reihenfolge der Liste, und eine
 * Bedingung sieht die Antworten davor schon so, wie sie hinausgehen. Eine verborgene Frage zaehlt fuer
 * die folgenden also mit ihrer Vorgabe, nicht mit dem, was frueher einmal gewaehlt war.
 */
export function visibleIds(list: QuestionList, answers: Answers): Set<string> {
  const seen: Record<string, AnswerValue | undefined> = { ...answers }
  const visible = new Set<string>()
  for (const question of list.questions) {
    if (isShown(question, seen)) visible.add(question.id)
    else seen[question.id] = defaultValue(question)
  }
  return visible
}

/** Passt ein gespeicherter Wert noch zur Frage? Sonst gilt die Vorgabe. */
function fits(question: Question, value: unknown): value is AnswerValue {
  switch (question.type) {
    case 'choice':
      return typeof value === 'string' && question.options.includes(value)
    case 'boolean':
      return typeof value === 'boolean'
    case 'languages':
      return isLanguageList(value) && value.length > 0
    case 'number':
      return (value === null && question.nullable) || (typeof value === 'number' && value >= question.min && value <= question.max)
    default:
      return value !== undefined
  }
}

/**
 * Die Antworten, mit denen der Assistent beginnt: die Vorgaben, darueber gelegt, was ein
 * gespeichertes Profil schon sagt.
 */
export function startAnswers(list: QuestionList, stored?: Partial<Record<string, unknown>> | null): ProfileAnswers {
  const answers: ProfileAnswers = { schema: list.schema, kind: list.kind }
  for (const question of list.questions) {
    const value = stored?.[question.id]
    answers[question.id] = fits(question, value) ? (question.type === 'languages' && isLanguageList(value) ? value.map((entry) => ({ ...entry })) : value) : defaultValue(question)
  }
  return answers
}

/** Was hinausgeht: jede Frage der Liste, verborgene mit ihrer Vorgabe. So speichert es auch der Server. */
export function effectiveAnswers(list: QuestionList, answers: ProfileAnswers): ProfileAnswers {
  const visible = visibleIds(list, answers)
  const result: ProfileAnswers = { schema: list.schema, kind: list.kind }
  for (const question of list.questions) {
    const value = answers[question.id]
    result[question.id] = visible.has(question.id) && value !== undefined ? value : defaultValue(question)
  }
  return result
}

/** Ein Schluessel fuer einen Satz Antworten, unabhaengig von der Reihenfolge der Felder. */
export function answersKey(answers: ProfileAnswers): string {
  return JSON.stringify(Object.keys(answers).sort().map((key) => [key, answers[key]]))
}

/** Die Rolle einer neu angehakten Sprache: Die erste ist Pflicht, jede weitere kommt gern dazu. */
function startRole(entries: readonly LanguageEntry[], roles: readonly LanguageRole[]): LanguageRole {
  const wanted: LanguageRole = entries.length === 0 ? 'required' : 'preferred'
  return roles.includes(wanted) ? wanted : (roles[0] ?? wanted)
}

/** Sprachen stehen in der Reihenfolge der Frage, nicht in der der Klicks. */
function inQuestionOrder(entries: LanguageEntry[], question: LanguagesQuestion): LanguageEntry[] {
  const position = (code: string) => {
    const index = question.codes.indexOf(code)
    return index === -1 ? question.codes.length : index
  }
  return [...entries].sort((left, right) => position(left.code) - position(right.code))
}

/** Eine Sprache an- oder abhaken. Abhaken darf die Liste leeren; weiter geht es dann erst mit einer Sprache. */
export function toggleLanguage(question: LanguagesQuestion, entries: readonly LanguageEntry[], code: string): LanguageEntry[] {
  if (entries.some((entry) => entry.code === code)) return entries.filter((entry) => entry.code !== code)
  return inQuestionOrder([...entries, { code, role: startRole(entries, question.roles) }], question)
}

export function setLanguageRole(entries: readonly LanguageEntry[], code: string, role: LanguageRole): LanguageEntry[] {
  return entries.map((entry) => (entry.code === code ? { ...entry, role } : entry))
}

/** Was im Zahlenfeld steht: ob "keine Grenze" gilt und der getippte Text. */
export type NumberDraft = { none: boolean; text: string }

export function draftFromValue(question: NumberQuestion, value: AnswerValue | undefined): NumberDraft {
  if (typeof value === 'number') return { none: false, text: String(value) }
  return { none: question.nullable, text: '' }
}

/**
 * Eine Zahl im erlaubten Bereich, mit hoechstens einer Nachkommastelle und Komma oder Punkt, oder null
 * bei "keine Grenze". Alles andere haelt den Assistenten an. Der Server nimmt auch Kommazahlen.
 */
export function readNumberDraft(question: NumberQuestion, draft: NumberDraft): { ok: true; value: number | null } | { ok: false } {
  if (draft.none && question.nullable) return { ok: true, value: null }
  const text = draft.text.trim().replace(',', '.')
  if (!/^\d{1,9}(\.\d)?$/.test(text)) return { ok: false }
  const value = Number(text)
  if (value < question.min || value > question.max) return { ok: false }
  return { ok: true, value }
}
