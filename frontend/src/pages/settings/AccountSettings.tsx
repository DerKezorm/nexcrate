import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { useAuth } from '../../auth/useAuth'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage, Section, SelectField } from '../../components/ui'
import { isLanguage, LANGUAGES } from '../../i18n/languages'
import { formatDate } from '../../lib/format'
import { Detail } from './parts'
import { PasswordForm } from './PasswordForm'

/** Das eine Konto: Name, Sprache, Passwort, abmelden. */
export function AccountSettings() {
  const { t, i18n } = useTranslation()
  const { me, setLanguage, logout, logoutAll } = useAuth()
  const [languageProblem, setLanguageProblem] = useState<unknown>(null)
  const [leaving, setLeaving] = useState<'here' | 'everywhere' | null>(null)
  const [leaveProblem, setLeaveProblem] = useState<unknown>(null)

  async function chooseLanguage(value: string) {
    if (!isLanguage(value)) return
    setLanguageProblem(null)
    try {
      await setLanguage(value)
    } catch (error) {
      setLanguageProblem(error)
    }
  }

  async function leave(where: 'here' | 'everywhere') {
    setLeaving(where)
    setLeaveProblem(null)
    try {
      // Klappt es, ersetzt die Anmeldeseite die App.
      await (where === 'here' ? logout() : logoutAll())
    } catch (error) {
      setLeaveProblem(error)
      setLeaving(null)
    }
  }

  if (!me) return null

  return (
    <div className="flex flex-col gap-4">
      <Section title={t('system.account.title')} intro={t('system.account.intro')}>
        <dl className="grid gap-3 sm:grid-cols-2">
          <Detail label={t('system.account.username')}>{me.username}</Detail>
          <Detail label={t('system.account.created')}>{formatDate(me.created_at, i18n.language)}</Detail>
        </dl>
        <div className="max-w-sm">
          <SelectField
            label={t('system.account.language')}
            hint={t('system.account.languageHint')}
            value={i18n.language}
            onChange={(event) => void chooseLanguage(event.target.value)}
          >
            {LANGUAGES.map(({ code, name }) => (
              <option key={code} value={code} lang={code}>
                {name}
              </option>
            ))}
          </SelectField>
        </div>
        {languageProblem !== null && <FormMessage>{errorText(t, languageProblem)}</FormMessage>}
      </Section>

      <PasswordForm username={me.username} />

      <Section title={t('system.account.sessions.title')} intro={t('system.account.sessions.intro')}>
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" onClick={() => void leave('here')} loading={leaving === 'here'} disabled={leaving !== null}>
            {leaving !== 'here' && <Symbol name="logout" />}
            {t('system.account.sessions.logout')}
          </Button>
          <Button variant="danger" onClick={() => void leave('everywhere')} loading={leaving === 'everywhere'} disabled={leaving !== null}>
            {t('system.account.sessions.logoutAll')}
          </Button>
        </div>
        {leaveProblem !== null && <FormMessage>{errorText(t, leaveProblem)}</FormMessage>}
      </Section>
    </div>
  )
}
