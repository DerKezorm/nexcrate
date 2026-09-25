import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../api/client'
import { characterCount, PASSWORD_MIN, USERNAME_MAX } from '../auth/password'
import { useAuth } from '../auth/useAuth'
import { AuthFrame } from '../components/AuthFrame'
import { PasswordField } from '../components/PasswordField'
import { Symbol } from '../components/Symbol'
import { Button, Card, Field, FormMessage } from '../components/ui'
import { FALLBACK_LANGUAGE, isLanguage, type Language } from '../i18n/languages'
import { RestoreBackup } from './settings/RestoreBackup'

/**
 * Erster Start: das eine Konto anlegen. Danach schliesst der Server die
 * Einrichtung fuer immer. Die Seite sagt das vorher in einfachen Worten.
 * Das Passwort wird zweimal abgefragt: Ein Vertipper hier sperrt aus, und
 * zurueck geht es dann nur noch ueber die Kommandozeile auf dem Server.
 *
 * Die Sprache waehlt man oben, wie auf der Anmeldeseite. Das Konto bekommt die
 * Sprache, in der die Seite beim Anlegen steht.
 */
export function SetupPage() {
  const { t, i18n } = useTranslation()
  const { setup } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [fromBackup, setFromBackup] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    const name = username.trim()
    if (name === '' || characterCount(name) > USERNAME_MAX) {
      setProblem(t('errors.username_invalid'))
      return
    }
    if (characterCount(password) < PASSWORD_MIN) {
      setProblem(t('errors.password_too_short', { min: PASSWORD_MIN }))
      return
    }
    if (password !== repeat) {
      setProblem(t('auth.setup.mismatch'))
      return
    }
    setProblem(null)
    setBusy(true)
    const language: Language = isLanguage(i18n.language) ? i18n.language : FALLBACK_LANGUAGE
    try {
      await setup(name, password, language)
    } catch (error) {
      setProblem(errorText(t, error))
      setBusy(false)
    }
  }

  return (
    <AuthFrame>
      <Card>
        <h1 className="text-2xl font-bold tracking-tight">
          {t('auth.setup.title')}
          <span className="text-accent-500">.</span>
        </h1>
        <p className="mt-1.5 text-sm text-mist-500">{t('auth.setup.intro')}</p>
        <p className="mt-2 flex items-start gap-2 text-xs text-mist-500">
          <Symbol name="globe" className="mt-px h-4 w-4 shrink-0" />
          <span>{t('auth.setup.languageHint')}</span>
        </p>

        <div className="mt-5 flex items-start gap-3 rounded-xl border border-accent-500/30 bg-accent-500/5 p-3.5">
          <Symbol name="shield" className="mt-0.5 h-5 w-5 shrink-0 text-accent-400" />
          <div className="min-w-0 text-sm">
            <p className="font-semibold text-mist-100">{t('auth.setup.onlyAccountTitle')}</p>
            <p className="mt-0.5 text-mist-400">{t('auth.setup.onlyAccount')}</p>
          </div>
        </div>

        <form onSubmit={(event) => void submit(event)} className="mt-5 flex flex-col gap-4" noValidate>
          <Field
            label={t('auth.fields.username')}
            hint={t('auth.setup.usernameHint', { max: USERNAME_MAX })}
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            maxLength={USERNAME_MAX}
            autoFocus
            required
          />
          <PasswordField
            label={t('auth.fields.password')}
            hint={t('auth.fields.passwordHint', { min: PASSWORD_MIN })}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="new-password"
            required
          />
          <PasswordField
            label={t('auth.fields.passwordRepeat')}
            value={repeat}
            onChange={(event) => setRepeat(event.target.value)}
            autoComplete="new-password"
            required
          />
          {problem && <FormMessage>{problem}</FormMessage>}
          <Button type="submit" loading={busy} className="mt-1 w-full">
            {t('auth.setup.submit')}
          </Button>
        </form>
      </Card>

      {/* Wie in Nexview: eine frische Installation kann statt leer mit einer heruntergeladenen Sicherung beginnen.
          Das Konto kommt dann aus der Sicherung. */}
      <Card className="mt-4">
        <h2 className="text-base font-semibold">{t('restore.setupTitle')}</h2>
        <p className="mt-1 text-sm text-mist-500">{t('restore.setupIntro')}</p>
        <div className="mt-4 flex flex-col gap-4">
          {fromBackup ? (
            <RestoreBackup fresh />
          ) : (
            <Button variant="ghost" className="self-start" onClick={() => setFromBackup(true)}>
              {t('restore.open')}
            </Button>
          )}
        </div>
      </Card>
    </AuthFrame>
  )
}
