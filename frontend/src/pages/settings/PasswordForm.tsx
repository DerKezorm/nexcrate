import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { authApi } from '../../api/auth'
import { errorText } from '../../api/client'
import { characterCount, PASSWORD_MIN } from '../../auth/password'
import { PasswordField } from '../../components/PasswordField'
import { Button, FormMessage, Section } from '../../components/ui'

/** Passwort aendern. Der Server meldet danach alle anderen Geraete ab, dieses bleibt angemeldet. */
export function PasswordForm({ username }: { username: string }) {
  const { t } = useTranslation()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    setDone(false)
    if (current === '') {
      setProblem(t('system.account.password.currentMissing'))
      return
    }
    if (characterCount(next) < PASSWORD_MIN) {
      setProblem(t('errors.password_too_short', { min: PASSWORD_MIN }))
      return
    }
    if (next !== repeat) {
      setProblem(t('system.account.password.mismatch'))
      return
    }
    setProblem(null)
    setBusy(true)
    try {
      await authApi.changePassword(current, next)
      setCurrent('')
      setNext('')
      setRepeat('')
      setDone(true)
    } catch (error) {
      // password_wrong und password_too_short {min} kommen mit Text aus errors.json.
      setProblem(errorText(t, error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section title={t('system.account.password.title')} intro={t('system.account.password.intro')}>
      <form onSubmit={(event) => void submit(event)} className="flex max-w-md flex-col gap-4" noValidate>
        {/* Fuer Passwortmanager: Sie ordnen das neue Passwort so dem richtigen Konto zu. */}
        <input type="text" name="username" autoComplete="username" value={username} readOnly hidden />
        <PasswordField
          label={t('system.account.password.current')}
          value={current}
          onChange={(event) => setCurrent(event.target.value)}
          autoComplete="current-password"
        />
        <PasswordField
          label={t('system.account.password.new')}
          hint={t('auth.fields.passwordHint', { min: PASSWORD_MIN })}
          value={next}
          onChange={(event) => setNext(event.target.value)}
          autoComplete="new-password"
        />
        <PasswordField
          label={t('system.account.password.repeat')}
          value={repeat}
          onChange={(event) => setRepeat(event.target.value)}
          autoComplete="new-password"
        />
        {problem && <FormMessage>{problem}</FormMessage>}
        {done && <FormMessage tone="ok">{t('system.account.password.done')}</FormMessage>}
        <div>
          <Button type="submit" loading={busy}>
            {t('system.account.password.submit')}
          </Button>
        </div>
      </form>
    </Section>
  )
}
