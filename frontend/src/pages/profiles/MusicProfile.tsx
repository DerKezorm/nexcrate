import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, errorText } from '../../api/client'
import { musicApi } from '../../api/music'
import type { AnswerValue, MusicProfile, MusicProfileSummary, ProfileAnswers, QuestionList } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Badge, Button, FormMessage, Spinner } from '../../components/ui'
import { musicStepText } from '../../lib/musicSteps'
import { Tile, TileHeader } from '../settings/parts'
import { musicProfileSentence, musicRoleText } from './musicProfileText'
import { QuestionField } from './QuestionField'
import { startAnswers } from './questions'

/** Die Leiter der fuenf Stufen: Ziel gruen, Vorerst Bernstein, Nein rosa. */
export function MusicLadder({ summary }: { summary: MusicProfileSummary }) {
  const { t } = useTranslation()
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label={t('music.profile.ladderLabel')}>
      {summary.ladder.map((entry) => (
        <li key={entry.step}>
          <Badge tone={entry.role === 'target' ? 'ok' : entry.role === 'for_now' ? 'accent' : 'bad'}>
            {t('music.profile.chip', { step: musicStepText(t, entry.step), role: musicRoleText(t, entry.role) })}
          </Badge>
        </li>
      ))}
    </ul>
  )
}

/**
 * Das Profil der Musik-Fassung (Musik M2): ein Absatz, die Leiter, die festen Regeln, und "Profil aendern". Die
 * Fassung der Filme und Serien baut gegen TRaSH und hat einen Assistenten mit Seiten; Musik hat vier Fragen in einem
 * Fenster, und die Zusammenfassung darunter folgt jeder Antwort. `changed` zaehlt hoch, wenn jemand anders (der
 * Pruefer) das Fenster oeffnen will.
 */
export function MusicProfileCard({ openRequest = 0, onChanged }: { openRequest?: number; onChanged?: () => void }) {
  const { t } = useTranslation()
  // undefined: laedt noch. null: es gibt kein Profil.
  const [profile, setProfile] = useState<MusicProfile | null | undefined>(undefined)
  const [problem, setProblem] = useState<unknown>(null)
  const [open, setOpen] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    const abort = new AbortController()
    musicApi.profile(abort.signal).then(
      // Eine Antwort ohne Leiter (aelterer Server) heisst "kein Profil", statt die Seite zu kippen.
      (result) => setProfile(result && Array.isArray(result.summary?.ladder) ? result : null),
      (error: unknown) => {
        if (abort.signal.aborted) return
        if (error instanceof ApiError && error.status === 404) setProfile(null)
        else setProblem(error)
      },
    )
    return () => abort.abort()
  }, [])

  useEffect(() => {
    if (openRequest > 0) setOpen(true)
  }, [openRequest])

  function done(next: MusicProfile | null) {
    setProfile(next)
    setOpen(false)
    setSaved(next !== null)
    onChanged?.()
  }

  return (
    <Tile>
      <TileHeader title={t('music.profile.title')} sub={t('music.profile.intro')}>
        {profile !== undefined && (
          <Button size="sm" variant={profile === null ? 'primary' : 'ghost'} onClick={() => setOpen(true)}>
            {profile === null ? t('music.profile.setUp') : t('music.profile.change')}
          </Button>
        )}
      </TileHeader>
      {profile === undefined ? (
        problem !== null ? (
          <FormMessage>{errorText(t, problem)}</FormMessage>
        ) : (
          <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
            <Spinner />
            {t('common.loading')}
          </p>
        )
      ) : profile === null ? (
        <p className="text-sm text-mist-400">{t('music.profile.none')}</p>
      ) : (
        <>
          <p className="text-sm leading-relaxed text-mist-200">{musicProfileSentence(t, profile.summary)}</p>
          <MusicLadder summary={profile.summary} />
        </>
      )}
      <FixedRules />
      <p className="text-xs leading-relaxed text-mist-500">{t('music.profile.library')}</p>
      {saved && (
        <p className="text-sm text-ok-500" role="status">
          {t('music.profile.saved')}
        </p>
      )}
      {open && profile !== undefined && <MusicProfileDialog stored={profile} onClose={() => setOpen(false)} onDone={done} />}
    </Tile>
  )
}

function FixedRules() {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-1">
      <h4 className="text-xs font-semibold text-mist-400">{t('music.profile.fixedLabel')}</h4>
      <ul className="flex list-disc flex-col gap-0.5 pl-5 text-xs text-mist-400">
        <li>{t('music.profile.fixed.cue')}</li>
        <li>{t('music.profile.fixed.tracks')}</li>
        <li>{t('music.profile.fixed.several')}</li>
        <li>{t('music.profile.fixed.audiobooks')}</li>
      </ul>
    </div>
  )
}

function MusicProfileDialog({ stored, onClose, onDone }: { stored: MusicProfile | null; onClose: () => void; onDone: (profile: MusicProfile | null) => void }) {
  const { t } = useTranslation()
  const [list, setList] = useState<QuestionList | null>(null)
  const [answers, setAnswers] = useState<ProfileAnswers | null>(null)
  const [summary, setSummary] = useState<MusicProfileSummary | null>(stored?.summary ?? null)
  const [problem, setProblem] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [confirmRemove, setConfirmRemove] = useState(false)
  // Nur die neueste Vorschau zaehlt. Eine langsame alte ueberschreibt keine neuere.
  const generation = useRef(0)

  useEffect(() => {
    const abort = new AbortController()
    musicApi.profileQuestions(abort.signal).then(
      (result) => {
        // Ohne Fragen gibt es nichts zu beantworten: das sagt die Meldung, statt ein leeres Fenster zu zeigen.
        if (!result || !Array.isArray(result.questions)) return setProblem(new Error('no questions'))
        setList(result)
        setAnswers(startAnswers(result, stored?.answers ?? null))
      },
      (error: unknown) => {
        if (!abort.signal.aborted) setProblem(error)
      },
    )
    return () => abort.abort()
  }, [stored])

  useEffect(() => {
    if (answers === null) return
    const current = ++generation.current
    musicApi.previewProfile(answers).then(
      (result) => {
        if (current === generation.current) setSummary(result.summary)
      },
      (error: unknown) => {
        if (current === generation.current) setProblem(error)
      },
    )
  }, [answers])

  function change(id: string, value: AnswerValue) {
    setProblem(null)
    setAnswers((before) => (before === null ? before : { ...before, [id]: value }))
  }

  async function save() {
    if (answers === null) return
    setBusy(true)
    setProblem(null)
    try {
      onDone(await musicApi.saveProfile(answers))
    } catch (error: unknown) {
      setProblem(error)
      setBusy(false)
    }
  }

  async function remove() {
    setBusy(true)
    setProblem(null)
    try {
      await musicApi.removeProfile()
      onDone(null)
    } catch (error: unknown) {
      setProblem(error)
      setBusy(false)
    }
  }

  return (
    <Dialog
      open
      wide
      title={t('music.profile.dialogTitle')}
      onClose={onClose}
      footer={
        <>
          {stored !== null &&
            (confirmRemove ? (
              <Button variant="danger" onClick={() => void remove()} disabled={busy}>
                {t('music.profile.removeConfirm')}
              </Button>
            ) : (
              <Button variant="ghost" onClick={() => setConfirmRemove(true)} disabled={busy}>
                {t('music.profile.remove')}
              </Button>
            ))}
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void save()} loading={busy} disabled={answers === null}>
            {t('music.profile.save')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-6">
        <p className="text-sm text-mist-500">{t('music.profile.dialogIntro')}</p>
        {list === null || answers === null ? (
          problem === null && (
            <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
              <Spinner />
              {t('common.loading')}
            </p>
          )
        ) : (
          list.questions.map((question) => (
            <QuestionField key={question.id} kind="album" question={question} value={answers[question.id]} draft={null} onChange={(value) => change(question.id, value)} onDraft={() => undefined} />
          ))
        )}
        {summary !== null && (
          <div className="flex flex-col gap-3 border-t border-ink-700 pt-4" aria-live="polite">
            <p className="text-sm leading-relaxed text-mist-200">{musicProfileSentence(t, summary)}</p>
            <MusicLadder summary={summary} />
          </div>
        )}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}
