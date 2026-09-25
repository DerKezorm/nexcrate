import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'

import { ApiError, errorText } from '../../api/client'
import { profilesApi } from '../../api/profiles'
import type { AnswerValue, MediaKind, NumberQuestion, ProfileAnswers, ProfilePreview, QuestionList } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage, Spinner } from '../../components/ui'
import { ProfileSummaryView } from './ProfileSummary'
import { invalidAnswersText, lineFromAnswers } from './profileText'
import { QuestionField } from './QuestionField'
import { answersKey, draftFromValue, effectiveAnswers, isLanguageList, readNumberDraft, startAnswers, type NumberDraft } from './questions'
import { buildPages, type WizardPage, type WizardPageId } from './wizardPages'

function pageName(t: TFunction, id: WizardPageId): string {
  switch (id) {
    case 'mode':
      return t('profiles.wizard.pages.mode')
    case 'target':
      return t('profiles.wizard.pages.target')
    case 'source':
      return t('profiles.wizard.pages.source')
    case 'languages':
      return t('profiles.wizard.pages.languages')
    case 'details':
      return t('profiles.wizard.pages.details')
    case 'audio':
      return t('profiles.wizard.pages.audio')
    case 'video':
      return t('profiles.wizard.pages.video')
    case 'cuts_services':
      return t('profiles.wizard.pages.cuts_services')
    case 'anime':
      return t('profiles.wizard.pages.anime')
    case 'more':
      return t('profiles.wizard.pages.more')
    case 'summary':
      return t('profiles.wizard.pages.summary')
  }
}

type Loaded = { list: QuestionList; hasProfile: boolean; byHand: boolean }

/** Die Vorschau gehoert zu genau einem Satz Antworten. Aendern sich die Antworten, gilt sie nicht mehr. */
type PreviewState = { key: string; data: ProfilePreview } | { key: string; error: unknown }

/**
 * Ein Profil fuer eine Fassung aus Alltagsfragen, wie Nexviews Qualitaets-Assistent. Gezeichnet
 * wird die Fragenliste des Servers; welche Fragen zusammen auf einer Seite stehen, sagt
 * `wizardPages.ts`, was gerade gefragt wird, `questions.ts`.
 *
 * Die letzte Seite baut das Profil am Server zur Probe (`preview`) und zeigt es in Worten.
 * ⚠️ Nur die Antwort auf die neueste Anfrage zaehlt: Wer zurueckgeht, etwas aendert und wieder
 * zum Ueberblick kommt, sieht nie die Zusammenfassung der alten Antworten.
 */
/**
 * Worauf der Assistent arbeitet: entweder das Profil selbst (der eine Ort) oder eine Fassung, die ihr Profil
 * dabei anlegt, wenn sie noch keines hat.
 */
export type WizardTarget = {
  kind: MediaKind
  /** Was in der Ueberschrift steht: der Name des Profils oder der Fassung. */
  label: string
  profileId?: number
  versionId?: number
  /** Ob schon etwas darin steht; ohne das faengt der Assistent bei den Vorgaben an. */
  filled?: boolean
}

export function ProfileWizard({ target, onClose, onDone }: { target: WizardTarget; onClose: () => void; onDone: () => void }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [answers, setAnswers] = useState<ProfileAnswers | null>(null)
  // Was in Zahlenfeldern steht, auch halb getippt. In den Antworten steht nur eine gueltige Zahl.
  const [drafts, setDrafts] = useState<Record<string, NumberDraft>>({})
  const [pageId, setPageId] = useState<WizardPageId>('mode')
  const [preview, setPreview] = useState<PreviewState | null>(null)
  const generation = useRef(0)
  const lastIndex = useRef(0)
  const top = useRef<HTMLDivElement>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const [askRemove, setAskRemove] = useState(false)

  useEffect(() => {
    let current = true
    async function load() {
      const list = await profilesApi.questions(target.kind)
      let stored: ProfileAnswers | null = null
      let byHand = false
      if (target.filled) {
        try {
          const profile = target.profileId !== undefined ? await profilesApi.readProfile(target.profileId) : await profilesApi.get(target.versionId ?? 0)
          stored = profile.empty === true ? null : profile.answers
          // Seit dem Expertenmodus: Speichern hier nimmt dem Besitzer, was er von Hand gesetzt hat.
          byHand = profile.mode === 'expert'
        } catch (error) {
          // Inzwischen entfernt: Dann beginnt der Assistent mit den Vorgaben.
          if (!(error instanceof ApiError && error.status === 404)) throw error
        }
      }
      if (!current) return
      setLoaded({ list, hasProfile: stored !== null, byHand })
      setAnswers(startAnswers(list, stored))
    }
    load().catch((error: unknown) => {
      if (current) setLoadError(error)
    })
    return () => {
      current = false
    }
  }, [target.profileId, target.versionId, target.kind, target.filled])

  const list = loaded?.list ?? null
  const pages = useMemo(() => (list && answers ? buildPages(list, answers) : []), [list, answers])
  const effective = useMemo(() => (list && answers ? effectiveAnswers(list, answers) : null), [list, answers])
  const key = useMemo(() => (effective ? answersKey(effective) : ''), [effective])

  // Faellt die aktuelle Seite weg, bleibt der Assistent an derselben Stelle stehen.
  let index = pages.findIndex((page) => page.id === pageId)
  if (index === -1) index = Math.min(lastIndex.current, Math.max(0, pages.length - 1))
  lastIndex.current = index
  const page: WizardPage | undefined = pages[index]
  const onSummary = page?.id === 'summary'

  useEffect(() => {
    if (!onSummary || effective === null || preview?.key === key) return
    const run = ++generation.current
    profilesApi.preview({ version_id: target.versionId ?? 0, answers: effective }).then(
      (data) => {
        if (run === generation.current) setPreview({ key, data })
      },
      (error: unknown) => {
        if (run === generation.current) setPreview({ key, error })
      },
    )
  }, [onSummary, effective, key, preview, target.versionId])

  // Eine neue Seite beginnt oben, auch wenn die vorige weit gescrollt war.
  useEffect(() => {
    top.current?.scrollIntoView?.({ block: 'nearest' })
  }, [pageId])

  function draftOf(question: NumberQuestion): NumberDraft {
    return drafts[question.id] ?? draftFromValue(question, answers?.[question.id])
  }

  function blocked(target: WizardPage): boolean {
    return target.questions.some((question) => {
      if (question.type === 'languages') {
        const value = answers?.[question.id]
        return !isLanguageList(value) || value.length === 0
      }
      if (question.type === 'number') return !readNumberDraft(question, draftOf(question)).ok
      return false
    })
  }

  function change(id: string, value: AnswerValue) {
    setAnswers((current) => (current ? { ...current, [id]: value } : current))
    setProblem(null)
  }

  function changeDraft(question: NumberQuestion, draft: NumberDraft) {
    setDrafts((current) => ({ ...current, [question.id]: draft }))
    const read = readNumberDraft(question, draft)
    if (read.ok) change(question.id, read.value)
  }

  function go(step: number) {
    const target = pages[index + step]
    if (!target) return
    setPageId(target.id)
    setProblem(null)
  }

  function close() {
    if (!busy) onClose()
  }

  async function save() {
    if (busy || effective === null) return
    setBusy(true)
    setProblem(null)
    try {
      if (target.profileId !== undefined) await profilesApi.saveProfile(target.profileId, effective)
      else await profilesApi.save(target.versionId ?? 0, effective)
      onDone()
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  async function remove() {
    if (busy) return
    setBusy(true)
    setProblem(null)
    try {
      await profilesApi.remove(target.versionId ?? 0)
      onDone()
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  function problemText(error: unknown): string {
    return error instanceof ApiError && error.code === 'profile_answers_invalid' ? invalidAnswersText(t, i18n, error, language) : errorText(t, error)
  }

  const previewData = preview !== null && preview.key === key && 'data' in preview ? preview.data : null
  const previewError = preview !== null && preview.key === key && 'error' in preview ? preview.error : null

  const title = askRemove
    ? t('profiles.wizard.removeTitle', { label: target.label })
    : target.filled
      ? t('profiles.wizard.titleEdit', { label: target.label })
      : t('profiles.wizard.titleNew', { label: target.label })

  let body: ReactNode
  let footer: ReactNode
  if (askRemove) {
    body = (
      <div className="flex flex-col gap-3">
        <p className="text-sm text-mist-300">{t('profiles.wizard.removeText', { label: target.label })}</p>
        {problem !== null && <FormMessage>{problemText(problem)}</FormMessage>}
      </div>
    )
    footer = (
      <>
        <Button
          variant="ghost"
          onClick={() => {
            setAskRemove(false)
            setProblem(null)
          }}
          disabled={busy}
        >
          {t('common.actions.cancel')}
        </Button>
        <Button variant="danger" onClick={() => void remove()} loading={busy}>
          {t('profiles.wizard.removeConfirm')}
        </Button>
      </>
    )
  } else if (list === null || answers === null || page === undefined) {
    body =
      loadError !== null ? (
        <FormMessage>{errorText(t, loadError)}</FormMessage>
      ) : (
        <p className="flex items-center justify-center gap-2 py-8 text-sm text-mist-500" role="status">
          <Spinner />
          {t('common.loading')}
        </p>
      )
    footer = (
      <Button variant="ghost" onClick={close}>
        {t('common.actions.cancel')}
      </Button>
    )
  } else {
    body = (
      <div ref={top} className="flex flex-col gap-5">
        <ol aria-label={t('profiles.wizard.progress')} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-mist-500">
          {pages.map((entry, position) => (
            <li key={entry.id} aria-current={position === index ? 'step' : undefined} className="flex items-center gap-2">
              <span className={position === index ? 'font-semibold text-accent-400' : position < index ? 'text-ok-500' : ''}>
                <span className="tabular-nums">{position + 1}</span> {pageName(t, entry.id)}
              </span>
              {position < pages.length - 1 && <Symbol name="chevron" className="h-3 w-3 text-mist-600" />}
            </li>
          ))}
        </ol>

        {/* Seit dem Expertenmodus: Wer hier speichert, verliert, was er im Profil von Hand gesetzt hat. */}
        {loaded?.byHand === true && <FormMessage tone="bad">{t('profiles.wizard.byHandWarning')}</FormMessage>}

        {page.id === 'summary' ? (
          previewData !== null ? (
            <ProfileSummaryView summary={previewData.summary} line={lineFromAnswers(list, previewData.answers)} />
          ) : previewError !== null ? (
            <div className="flex flex-col items-start gap-3">
              <FormMessage>{problemText(previewError)}</FormMessage>
              <Button variant="ghost" size="sm" onClick={() => setPreview(null)}>
                <Symbol name="refresh" />
                {t('common.actions.retry')}
              </Button>
            </div>
          ) : (
            <p className="flex items-center justify-center gap-2 py-8 text-sm text-mist-500" role="status">
              <Spinner />
              {t('profiles.wizard.building')}
            </p>
          )
        ) : (
          <>
            {page.id === 'more' && <p className="text-sm text-mist-400">{t('profiles.wizard.moreIntro')}</p>}
            {page.questions.map((question) => (
              <QuestionField
                key={question.id}
                question={question}
                value={answers[question.id]}
                draft={question.type === 'number' ? draftOf(question) : null}
                onChange={(value) => change(question.id, value)}
                onDraft={(draft) => {
                  if (question.type === 'number') changeDraft(question, draft)
                }}
                kind={target.kind}
              />
            ))}
          </>
        )}

        {problem !== null && <FormMessage>{problemText(problem)}</FormMessage>}
      </div>
    )
    footer = (
      <>
        {loaded?.hasProfile && target.versionId !== undefined && (
          <div className="mr-auto">
            <Button
              variant="danger"
              onClick={() => {
                setAskRemove(true)
                setProblem(null)
              }}
              disabled={busy}
            >
              <Symbol name="trash" />
              {t('profiles.wizard.remove')}
            </Button>
          </div>
        )}
        <Button variant="ghost" onClick={() => (index === 0 ? close() : go(-1))} disabled={busy}>
          {index === 0 ? t('common.actions.cancel') : t('profiles.wizard.back')}
        </Button>
        {onSummary ? (
          <Button onClick={() => void save()} loading={busy} disabled={previewData === null}>
            {t('profiles.wizard.save')}
          </Button>
        ) : (
          <Button onClick={() => go(1)} disabled={blocked(page)}>
            {t('profiles.wizard.next')}
            <Symbol name="arrow" />
          </Button>
        )}
      </>
    )
  }

  return (
    <Dialog open wide title={title} onClose={close} footer={footer}>
      {body}
    </Dialog>
  )
}
