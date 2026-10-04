import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { totpApi } from '../../api/auth'
import { errorText } from '../../api/client'
import type { TotpEnrolment, TotpState } from '../../api/types'
import { PasswordField } from '../../components/PasswordField'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, Field, FormMessage, Section, Spinner } from '../../components/ui'

/** Was gerade offen ist: Einrichten, Ausschalten oder neue Wiederherstellungscodes, jeweils mit Passwort. */
type Step = { kind: 'idle' } | { kind: 'enrol'; enrolment: TotpEnrolment } | { kind: 'disable' } | { kind: 'recovery' } | { kind: 'codes'; codes: string[] }

/** Den Schluessel in Vierergruppen, zum Abtippen, wenn die Kamera nicht hilft. */
function grouped(seed: string): string {
  return seed.replace(/(.{4})/g, '$1 ').trim()
}

/**
 * Der zweite Faktor fuer die Anmeldung mit Passwort: Codes aus einer Authenticator-App plus acht
 * Wiederherstellungscodes. Schluessel und Codes zeigt die Seite genau einmal. Wer sich ueber einen Anbieter
 * anmeldet, bringt dessen zweiten Faktor mit; hier wird dann nichts gefragt.
 */
export function TwoFactorSection({ emergencyOnly = false }: { emergencyOnly?: boolean } = {}) {
  const { t } = useTranslation()
  const [state, setState] = useState<TotpState | null>(null)
  const [step, setStep] = useState<Step>({ kind: 'idle' })
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  const load = useCallback(async () => {
    try {
      setState(await totpApi.state())
    } catch (error) {
      setProblem(error)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  function reset(next: Step = { kind: 'idle' }) {
    setStep(next)
    setCode('')
    setPassword('')
    setProblem(null)
  }

  async function begin() {
    setBusy(true)
    setProblem(null)
    try {
      reset({ kind: 'enrol', enrolment: await totpApi.begin() })
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setProblem(null)
    try {
      if (step.kind === 'enrol') {
        const { recovery_codes } = await totpApi.finish(code.trim(), password)
        reset({ kind: 'codes', codes: recovery_codes })
      } else if (step.kind === 'recovery') {
        const { recovery_codes } = await totpApi.recovery(password)
        reset({ kind: 'codes', codes: recovery_codes })
      } else if (step.kind === 'disable') {
        await totpApi.disable(password)
        reset()
      }
      await load()
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  function save(codes: string[]) {
    const text = `${t('system.account.twoFactor.fileHeader')}\n\n${codes.join('\n')}\n`
    const link = document.createElement('a')
    link.href = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
    link.download = 'nexcrate-recovery-codes.txt'
    link.click()
    URL.revokeObjectURL(link.href)
  }

  const passwordField = (
    <PasswordField
      label={t('system.account.twoFactor.password')}
      value={password}
      onChange={(event) => setPassword(event.target.value)}
      autoComplete="current-password"
    />
  )

  return (
    <Section
      title={t('system.account.twoFactor.title')}
      intro={t('system.account.twoFactor.intro')}
      actions={state?.enabled ? <Badge tone="ok">{t('system.account.twoFactor.on')}</Badge> : undefined}
    >
      {emergencyOnly && <FormMessage tone="info">{t('system.account.emergencyOnly')}</FormMessage>}
      {state === null && problem === null && (
        <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
          <Spinner />
          {t('common.loading')}
        </p>
      )}

      {step.kind === 'codes' ? (
        <div className="flex flex-col gap-3">
          <FormMessage tone="info">{t('system.account.twoFactor.codesOnce')}</FormMessage>
          <ul className="grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label={t('system.account.twoFactor.codesLabel')}>
            {step.codes.map((recovery) => (
              <li key={recovery} className="rounded-lg border border-ink-700 bg-ink-900/60 px-3 py-2 text-center font-mono text-sm text-mist-100">
                {recovery}
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" onClick={() => save(step.codes)}>
              <Symbol name="download" />
              {t('system.account.twoFactor.save')}
            </Button>
            <Button onClick={() => reset()}>{t('system.account.twoFactor.done')}</Button>
          </div>
        </div>
      ) : step.kind === 'enrol' ? (
        <form onSubmit={(event) => void submit(event)} className="flex flex-col gap-4" noValidate>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
            <img
              src={`data:image/svg+xml;utf8,${encodeURIComponent(step.enrolment.qr_svg)}`}
              alt={t('system.account.twoFactor.qr')}
              className="h-44 w-44 shrink-0 rounded-lg bg-white"
            />
            <div className="flex min-w-0 flex-col gap-2 text-sm text-mist-400">
              <p>{t('system.account.twoFactor.scan')}</p>
              <p>{t('system.account.twoFactor.manual')}</p>
              <p className="font-mono text-mist-100 wrap-anywhere select-all">{grouped(step.enrolment.seed)}</p>
            </div>
          </div>
          <div className="grid max-w-xl gap-4 sm:grid-cols-2">
            <Field
              label={t('system.account.twoFactor.code')}
              value={code}
              onChange={(event) => setCode(event.target.value)}
              autoComplete="one-time-code"
              inputMode="numeric"
              maxLength={8}
            />
            {passwordField}
          </div>
          {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" loading={busy} disabled={code.trim() === '' || password === ''}>
              {t('system.account.twoFactor.confirm')}
            </Button>
            <Button variant="ghost" onClick={() => reset()}>
              {t('common.actions.cancel')}
            </Button>
          </div>
        </form>
      ) : step.kind === 'disable' || step.kind === 'recovery' ? (
        <form onSubmit={(event) => void submit(event)} className="flex max-w-md flex-col gap-4" noValidate>
          <p className="text-sm text-mist-400">
            {step.kind === 'disable' ? t('system.account.twoFactor.disableIntro') : t('system.account.twoFactor.recoveryIntro')}
          </p>
          {passwordField}
          {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant={step.kind === 'disable' ? 'danger' : 'primary'} loading={busy} disabled={password === ''}>
              {step.kind === 'disable' ? t('system.account.twoFactor.disable') : t('system.account.twoFactor.newCodes')}
            </Button>
            <Button variant="ghost" onClick={() => reset()}>
              {t('common.actions.cancel')}
            </Button>
          </div>
        </form>
      ) : state?.enabled ? (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-mist-400">{t('system.account.twoFactor.left', { count: state.recovery_codes_left })}</p>
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" onClick={() => reset({ kind: 'recovery' })}>
              {t('system.account.twoFactor.newCodes')}
            </Button>
            <Button variant="danger" onClick={() => reset({ kind: 'disable' })}>
              {t('system.account.twoFactor.disable')}
            </Button>
          </div>
        </div>
      ) : (
        state !== null && (
          <div className="flex flex-col gap-3">
            <p className="text-sm text-mist-400">{t('system.account.twoFactor.offText')}</p>
            <div>
              <Button onClick={() => void begin()} loading={busy}>
                {!busy && <Symbol name="shield" />}
                {t('system.account.twoFactor.enable')}
              </Button>
            </div>
          </div>
        )
      )}
      {step.kind === 'idle' && problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
    </Section>
  )
}
