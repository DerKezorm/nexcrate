import type { AnswerValue, Question, QuestionList } from '../../api/types'
import { visibleIds } from './questions'

export type WizardPageId = 'mode' | 'target' | 'source' | 'languages' | 'details' | 'audio' | 'video' | 'cuts_services' | 'anime' | 'more' | 'summary'

/**
 * Welche Fragen zusammen auf einer Seite stehen, wie die Schritte in Nexview. Eine Seite, deren
 * Fragen gerade alle verborgen sind, faellt weg; so erscheinen Ton, Bild sowie Schnitt und Dienste
 * nur im ausfuehrlichen Modus.
 *
 * Die Seite `cuts_services` hiess bis zum Test von 2b (13.09.2026) "origin" und trug dazu die deutschen
 * Release-Gruppen. Die Frage ist gestrichen, der Name passte nicht mehr.
 *
 * ⚠️ Kennt die Tabelle eine Frage des Servers nicht, landet sie auf der Seite "more" vor dem
 * Ueberblick, statt zu verschwinden. Eine neue Frage braucht dann nur Texte, keinen neuen Code.
 */
export const PAGE_TABLE: readonly { id: Exclude<WizardPageId, 'more' | 'summary'>; questions: readonly string[] }[] = [
  { id: 'mode', questions: ['mode'] },
  { id: 'target', questions: ['resolution', 'take_now'] },
  { id: 'source', questions: ['source'] },
  { id: 'languages', questions: ['languages', 'required_languages'] },
  { id: 'details', questions: ['hdr', 'good_enough', 'max_gb_per_hour'] },
  { id: 'audio', questions: ['audio', 'accessibility'] },
  { id: 'video', questions: ['x265_hd', 'sdr'] },
  { id: 'cuts_services', questions: ['special_cuts', 'asian_services'] },
  // Seit Anime A4: nur bei Serien, wie die Fassung ihre Anime-Serien bewertet.
  { id: 'anime', questions: ['anime'] },
]

export type WizardPage = { id: WizardPageId; questions: Question[] }

/** Die Seiten zu den Antworten von jetzt. Was gilt, rechnet `visibleIds` in der Reihenfolge der Liste, wie der Server. */
export function buildPages(list: QuestionList, answers: Readonly<Record<string, AnswerValue | undefined>>): WizardPage[] {
  const visible = visibleIds(list, answers)
  const byId = new Map(list.questions.map((question) => [question.id, question]))
  const known = new Set(PAGE_TABLE.flatMap((page) => page.questions))
  const pages: WizardPage[] = []
  for (const entry of PAGE_TABLE) {
    const questions = entry.questions.flatMap((id) => {
      const question = byId.get(id)
      return question && visible.has(id) ? [question] : []
    })
    if (questions.length > 0) pages.push({ id: entry.id, questions })
  }
  const unknown = list.questions.filter((question) => !known.has(question.id) && visible.has(question.id))
  if (unknown.length > 0) pages.push({ id: 'more', questions: unknown })
  pages.push({ id: 'summary', questions: [] })
  return pages
}
