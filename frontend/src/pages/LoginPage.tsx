import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { authApi, oidcApi } from '../api/auth'
import { ApiError, errorText } from '../api/client'
import type { OidcState } from '../api/types'
import type { LoginNotice } from '../auth/AuthContext'
import { USERNAME_MAX } from '../auth/password'
import { useAuth } from '../auth/useAuth'
import { AuthFrame } from '../components/AuthFrame'
import { PasswordField } from '../components/PasswordField'
import { Symbol } from '../components/Symbol'
import { Button, Card, Field, FormMessage } from '../components/ui'
import { withBase } from '../lib/base'
import { useCountdown } from '../lib/useCountdown'
import { oidcErrorKey } from '../vendor/nexoidc/oidc'

/** Gesperrt bis `until`. `seconds` ist die Wartezeit, wie der Server sie nannte. */
type Lock = { until: number; seconds: number }

/** Der Weg zu einem Anbieter. Eine Seite des Servers, keine Anfrage: der Browser geht dorthin und kommt zurueck. */
function providerStart(slug: string): string {
  return withBase(`/api/oidc/${encodeURIComponent(slug)}/start`)
}

/**
 * Was der Rueckweg vom Anbieter in die Adresse schrieb: `?error=<code>` (nur Codes der gemeinsamen Anmeldung, nie ein
 * roher Text) und `?step=code`, wenn nach dem Anbieter noch der Code des zweiten Faktors fehlt. Einmal gelesen,
 * verschwindet es aus der Adresse, damit ein Neuladen es nicht wiederholt.
 */
function takeReturn(): { error: string | null; code: boolean } {
  const params = new URLSearchParams(window.location.search)
  const key = oidcErrorKey(params.get('error'))
  const code = params.get('step') === 'code'
  if (!params.has('error') && !params.has('step')) return { error: null, code: false }
  params.delete('error')
  params.delete('step')
  const rest = params.toString()
  window.history.replaceState(null, '', window.location.pathname + (rest ? `?${rest}` : '') + window.location.hash)
  return { error: key, code }
}

/**
 * Anmeldung. Nach zu vielen Fehlversuchen nennt der Server eine Wartezeit
 * (`login_throttled` mit `retry_after`). Die Seite zaehlt sie sichtbar herunter
 * und gibt den Knopf erst danach wieder frei, statt jeden Klick scheitern zu lassen.
 *
 * Aufbau wie in jeder nex-App (Bauplan 04): Passwortformular, darunter "oder", darunter je aktivem Anbieter ein Knopf
 * in der Reihenfolge der Liste. Ist die Anmeldung mit Passwort aus, bleiben nur die Knoepfe; der Rueckweg ist dann
 * `NEXCRATE_PASSWORD_LOGIN=1`. Mit zweitem Faktor folgt auf das Passwort (oder einen Anbieter, dem der zweite Faktor
 * nicht anvertraut ist) der Code.
 */
export function LoginPage({ notice }: { notice: LoginNotice | null }) {
  const { t } = useTranslation()
  const { login, loginCode } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [returned] = useState(() => takeReturn())
  const [askCode, setAskCode] = useState(returned.code)
  const [problem, setProblem] = useState<string | null>(null)
  const [lock, setLock] = useState<Lock | null>(null)
  const [busy, setBusy] = useState(false)
  const [tried, setTried] = useState(false)
  const [provider, setProvider] = useState<OidcState | null>(null)
  const remaining = useCountdown(lock?.until ?? null)
  const locked = lock !== null && remaining > 0

  useEffect(() => {
    let alive = true
    oidcApi
      .state()
      // Ohne Antwort bleibt das Formular fuer das Passwort, wie bisher.
      .then((answer) => alive && setProvider(answer))
      .catch(() => alive && setProvider({ password_login: true, providers: [] }))
    return () => {
      alive = false
    }
  }, [])

  function fail(error: unknown) {
    if (error instanceof ApiError && error.code === 'login_throttled') {
      const seconds = Math.max(1, Math.ceil(Number(error.values.retry_after) || 1))
      setLock({ until: Date.now() + seconds * 1000, seconds })
    } else if (error instanceof ApiError && error.code === 'totp_pending_missing') {
      // Fuenf falsche Codes oder zu lange gewartet: zurueck zum Passwort.
      setAskCode(false)
      setCode('')
      setPassword('')
      setProblem(errorText(t, error))
    } else {
      setProblem(errorText(t, error))
    }
    setBusy(false)
  }

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
      if ((await login(username.trim(), password)) === 'second_factor') {
        setAskCode(true)
        setBusy(false)
      }
    } catch (error) {
      fail(error)
    }
  }

  async function submitCode(event: FormEvent) {
    event.preventDefault()
    if (busy || locked) return
    setLock(null)
    if (code.trim() === '') {
      setProblem(t('auth.code.missing'))
      return
    }
    setProblem(null)
    setBusy(true)
    try {
      await loginCode(code.trim())
    } catch (error) {
      fail(error)
    }
  }

  const lockMessage = locked && lock && (
    <>
      {/* Sichtbar zaehlt es herunter, ohne jede Sekunde vorgelesen zu werden. Angesagt wird einmal. */}
      <FormMessage role="timer">{t('errors.login_throttled', { retry_after: remaining })}</FormMessage>
      <p className="sr-only" role="alert">
        {t('errors.login_throttled', { retry_after: lock.seconds })}
      </p>
    </>
  )

  if (askCode) {
    return (
      <AuthFrame>
        <Card>
          <h1 className="text-2xl font-bold tracking-tight">
            {t('auth.code.title')}
            <span className="text-accent-500">.</span>
          </h1>
          <p className="mt-1.5 text-sm text-mist-500">{t('auth.code.intro')}</p>
          <form onSubmit={(event) => void submitCode(event)} className="mt-6 flex flex-col gap-4" noValidate>
            <Field
              label={t('auth.code.label')}
              hint={t('auth.code.hint')}
              value={code}
              onChange={(event) => setCode(event.target.value)}
              autoComplete="one-time-code"
              inputMode="text"
              autoCapitalize="none"
              spellCheck={false}
              maxLength={32}
              autoFocus
            />
            {lockMessage}
            {problem && <FormMessage>{problem}</FormMessage>}
            <Button type="submit" loading={busy} disabled={locked} className="mt-1 w-full">
              {t('auth.code.submit')}
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                void authApi.cancelCode().catch(() => undefined)
                setAskCode(false)
                setCode('')
                setPassword('')
                setProblem(null)
              }}
            >
              <Symbol name="back" />
              {t('auth.code.back')}
            </Button>
          </form>
        </Card>
      </AuthFrame>
    )
  }

  // Nur ein ausdrueckliches Nein blendet das Formular aus; eine fremde oder halbe Antwort laesst es stehen.
  const showPassword = provider === null || provider.password_login !== false
  const buttons = Array.isArray(provider?.providers) ? provider.providers : []

  return (
    <AuthFrame>
      <Card>
        <h1 className="text-2xl font-bold tracking-tight">
          {t('auth.login.title')}
          <span className="text-accent-500">.</span>
        </h1>
        <p className="mt-1.5 text-sm text-mist-500">{t(showPassword ? 'auth.login.intro' : 'auth.login.introProvider')}</p>
        <div className="mt-6 flex flex-col gap-4">
          {notice && !tried && (
            <FormMessage tone="info">{notice === 'setupClosed' ? t('auth.login.setupClosed') : t('auth.login.sessionEnded')}</FormMessage>
          )}
          {returned.error && !tried && <FormMessage>{t(returned.error)}</FormMessage>}
          {showPassword && (
            <form onSubmit={(event) => void submit(event)} className="flex flex-col gap-4" noValidate>
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
              {lockMessage}
              {problem && <FormMessage>{problem}</FormMessage>}
              <Button type="submit" loading={busy} disabled={locked} className="mt-1 w-full">
                {t('auth.login.submit')}
              </Button>
            </form>
          )}
          {showPassword && buttons.length > 0 && (
            <p className="flex items-center gap-3 text-xs text-mist-600" aria-hidden="true">
              <span className="h-px flex-1 bg-ink-700" />
              {t('oidc.login.or')}
              <span className="h-px flex-1 bg-ink-700" />
            </p>
          )}
          {buttons.length > 0 && (
            <div className="flex flex-col gap-2" data-testid="provider-buttons">
              {buttons.map((entry) => (
                <Button
                  key={entry.slug}
                  variant={showPassword ? 'ghost' : 'primary'}
                  className="w-full"
                  onClick={() => window.location.assign(providerStart(entry.slug))}
                >
                  <Symbol name="shield" />
                  {t('oidc.login.button', { name: entry.label })}
                </Button>
              ))}
            </div>
          )}
        </div>
      </Card>
    </AuthFrame>
  )
}
