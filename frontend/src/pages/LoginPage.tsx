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
import { buttonClasses } from '../components/buttonClasses'
import { Button, Card, Field, FormMessage } from '../components/ui'
import { withBase } from '../lib/base'
import { useCountdown } from '../lib/useCountdown'

/** Gesperrt bis `until`. `seconds` ist die Wartezeit, wie der Server sie nannte. */
type Lock = { until: number; seconds: number }

/** Der Weg zum Anbieter. Eine Seite des Servers, keine Anfrage: der Browser geht dorthin und kommt zurueck. */
const PROVIDER_START = withBase('/api/oidc/start')

/**
 * Ein Fehler vom Rueckweg des Anbieters steht als `?oidc_error=<code>` in der Adresse. Einmal gelesen, verschwindet
 * er aus der Adresse, damit ein Neuladen ihn nicht wieder zeigt.
 */
function takeProviderError(): string | null {
  const params = new URLSearchParams(window.location.search)
  const code = params.get('oidc_error')
  if (!code || !/^[a-z0-9_]+$/.test(code)) return null
  params.delete('oidc_error')
  const rest = params.toString()
  window.history.replaceState(null, '', window.location.pathname + (rest ? `?${rest}` : '') + window.location.hash)
  return code
}

/**
 * Anmeldung. Nach zu vielen Fehlversuchen nennt der Server eine Wartezeit
 * (`login_throttled` mit `retry_after`). Die Seite zaehlt sie sichtbar herunter
 * und gibt den Knopf erst danach wieder frei, statt jeden Klick scheitern zu lassen.
 *
 * Ist ein Anbieter verknuepft (authentik und andere), steht sein Knopf oben. Das Formular fuer das Passwort fehlt,
 * wenn die Anmeldung mit Passwort ausgeschaltet ist. Mit zweitem Faktor folgt auf das Passwort der Code.
 */
export function LoginPage({ notice }: { notice: LoginNotice | null }) {
  const { t } = useTranslation()
  const { login, loginCode } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [askCode, setAskCode] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [lock, setLock] = useState<Lock | null>(null)
  const [busy, setBusy] = useState(false)
  const [tried, setTried] = useState(false)
  const [provider, setProvider] = useState<OidcState | null>(null)
  const [providerError] = useState<string | null>(() => takeProviderError())
  const remaining = useCountdown(lock?.until ?? null)
  const locked = lock !== null && remaining > 0

  useEffect(() => {
    let alive = true
    oidcApi
      .state()
      // Ohne Antwort bleibt das Formular fuer das Passwort, wie bisher.
      .then((answer) => alive && setProvider(answer))
      .catch(() => alive && setProvider({ enabled: false, provider_name: '', password_login: true }))
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
  const showProvider = provider !== null && provider.enabled === true

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
          {providerError && !tried && <FormMessage>{errorText(t, new ApiError(0, providerError))}</FormMessage>}
          {showProvider && (
            <a href={PROVIDER_START} className={`${buttonClasses(showPassword ? 'ghost' : 'primary', 'md')} w-full`}>
              <Symbol name="shield" />
              {t('auth.login.provider', { name: provider.provider_name || 'OpenID Connect' })}
            </a>
          )}
          {showProvider && showPassword && (
            <p className="flex items-center gap-3 text-xs text-mist-600" aria-hidden="true">
              <span className="h-px flex-1 bg-ink-700" />
              {t('auth.login.or')}
              <span className="h-px flex-1 bg-ink-700" />
            </p>
          )}
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
                autoFocus={!showProvider}
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
        </div>
      </Card>
    </AuthFrame>
  )
}
