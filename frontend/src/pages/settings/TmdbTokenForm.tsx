import { useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { tmdbApi } from '../../api/tmdb'
import type { TmdbState } from '../../api/types'
import { PasswordField } from '../../components/PasswordField'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage } from '../../components/ui'
import { cleanToken, looksLikeApiKey } from './tmdbToken'

const TMDB_LOGIN_URL = 'https://www.themoviedb.org/login'
const TMDB_API_SETTINGS_URL = 'https://www.themoviedb.org/settings/api'

function ExternalLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="inline-flex w-fit items-center gap-1 font-medium text-accent-400 hover:underline">
      {children}
      <Symbol name="link" className="h-3.5 w-3.5" />
    </a>
  )
}

function Step({ number, children }: { number: number; children: ReactNode }) {
  return (
    <li className="flex items-start gap-3">
      <span
        aria-hidden="true"
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-accent-500/40 bg-accent-500/10 text-xs font-bold text-accent-400 tabular-nums"
      >
        {number}
      </span>
      <div className="flex min-w-0 flex-col gap-0.5 pt-0.5">{children}</div>
    </li>
  )
}

/** Die vier Schritte zum Token, fuer jemanden, der TMDB noch nie gesehen hat. */
export function TmdbGuide() {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-ink-700 bg-ink-900/60 p-4">
      <p className="text-sm font-semibold text-mist-100">{t('tmdb.guide.title')}</p>
      <ol className="flex flex-col gap-3 text-sm text-mist-300">
        <Step number={1}>
          <span>{t('tmdb.guide.login')}</span>
          <ExternalLink href={TMDB_LOGIN_URL}>{t('tmdb.guide.loginLink')}</ExternalLink>
        </Step>
        <Step number={2}>
          <span>{t('tmdb.guide.settings')}</span>
          <ExternalLink href={TMDB_API_SETTINGS_URL}>{t('tmdb.guide.settingsLink')}</ExternalLink>
        </Step>
        <Step number={3}>
          <span>{t('tmdb.guide.copy')}</span>
        </Step>
        <Step number={4}>
          <span>{t('tmdb.guide.paste')}</span>
        </Step>
      </ol>
    </div>
  )
}

/**
 * Den TMDB-Token eintragen: pruefen und speichern in einem Schritt, der Server nimmt ihn nur,
 * wenn TMDB ihn annimmt. Steht im Reiter "TMDB" und im Dialog "Hinzufuegen", solange kein
 * Token da ist.
 *
 * ⚠️ Das Feld ist nie vorbelegt, der Server gibt den Token nie heraus. Nach dem Speichern
 * wird es sofort geleert; der Token steht nie in localStorage, in der Adresse oder im Protokoll.
 */
export function TmdbTokenForm({ configured, onSaved, guide = true }: { configured: boolean; onSaved: (state: TmdbState) => void; guide?: boolean }) {
  const { t } = useTranslation()
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  async function save() {
    if (busy) return
    const value = cleanToken(token)
    if (value === '') return setProblem(t('tmdb.missingToken'))
    if (looksLikeApiKey(value)) return setProblem(t('tmdb.looksLikeKey'))
    setBusy(true)
    setProblem(null)
    try {
      const state = await tmdbApi.save(value)
      setToken('')
      setBusy(false)
      onSaved(state)
    } catch (error) {
      // tmdb_token_rejected, tmdb_unreachable und die anderen TMDB-Codes kommen mit Text aus errors.json.
      setProblem(errorText(t, error))
      setBusy(false)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void save()
  }

  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-4">
      {guide && <TmdbGuide />}
      <PasswordField
        label={t('tmdb.token')}
        hint={configured ? t('tmdb.replaceHint') : t('tmdb.tokenHint')}
        value={token}
        onChange={(event) => {
          setToken(event.target.value)
          setProblem(null)
        }}
        autoComplete="new-password"
        maxLength={4000}
      />
      {problem && <FormMessage>{problem}</FormMessage>}
      <div>
        <Button type="submit" loading={busy}>
          {!busy && <Symbol name="check" />}
          {t('tmdb.save')}
        </Button>
      </div>
    </form>
  )
}
