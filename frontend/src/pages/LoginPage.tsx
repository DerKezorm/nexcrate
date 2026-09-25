import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, errorText } from '../api/client'
import type { LoginNotice } from '../auth/AuthContext'
import { USERNAME_MAX } from '../auth/password'
import { useAuth } from '../auth/useAuth'
import { AuthFrame } from '../components/AuthFrame'
import { PasswordField } from '../components/PasswordField'
import { Button, Card, Field, FormMessage } from '../components/ui'
import { useCountdown } from '../lib/useCountdown'

/** Gesperrt bis `until`. `seconds` ist die Wartezeit, wie der Server sie nannte. */
type Lock = { until: number; seconds: number }

/**
 * Anmeldung. Nach zu vielen Fehlversuchen nennt der Server eine Wartezeit
 * (`login_throttled` mit `retry_after`). Die Seite zaehlt sie sichtbar herunter
 * und gibt den Knopf erst danach wieder frei, statt jeden Klick scheitern zu lassen.
 */
export function LoginPage({ notice }: { notice: LoginNotice | null }) {
  const { t } = useTranslation()
  const { login } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [lock, setLock] = useState<Lock | null>(null)
  const [busy, setBusy] = useState(false)
  const [tried, setTried] = useState(false)
  const remaining = useCountdown(lock?.until ?? null)
  const locked = lock !== null && remaining > 0

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy || locked) return
    setTried(true)
    setLock(null)
    if (username.trim() === '' || password === '') {
      setProblem(t('auth.login.missing'))
      return
    }
    setProblem(null)
    setBusy(true)
    try {
      // Klappt es, ersetzt die App diese Seite.
      await login(username.trim(), password)
    } catch (error) {
      if (error instanceof ApiError && error.code === 'login_throttled') {
        const seconds = Math.max(1, Math.ceil(Number(error.values.retry_after) || 1))
        setLock({ until: Date.now() + seconds * 1000, seconds })
      } else {
        setProblem(errorText(t, error))
      }
      setBusy(false)
    }
  }

  return (
    <AuthFrame>
      <Card>
        <h1 className="text-2xl font-bold tracking-tight">
          {t('auth.login.title')}
          <span className="text-accent-500">.</span>
        </h1>
        <p className="mt-1.5 text-sm text-mist-500">{t('auth.login.intro')}</p>
        <form onSubmit={(event) => void submit(event)} className="mt-6 flex flex-col gap-4" noValidate>
          {notice && !tried && (
            <FormMessage tone="info">{notice === 'setupClosed' ? t('auth.login.setupClosed') : t('auth.login.sessionEnded')}</FormMessage>
          )}
          <Field
            label={t('auth.fields.username')}
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
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            required
          />
          {locked && (
            <>
              {/* Sichtbar zaehlt es herunter, ohne jede Sekunde vorgelesen zu werden. Angesagt wird einmal. */}
              <FormMessage role="timer">{t('errors.login_throttled', { retry_after: remaining })}</FormMessage>
              <p className="sr-only" role="alert">
                {t('errors.login_throttled', { retry_after: lock.seconds })}
              </p>
            </>
          )}
          {problem && <FormMessage>{problem}</FormMessage>}
          <Button type="submit" loading={busy} disabled={locked} className="mt-1 w-full">
            {t('auth.login.submit')}
          </Button>
        </form>
      </Card>
    </AuthFrame>
  )
}
