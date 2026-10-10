import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { oidcApi } from '../../api/auth'
import { errorText } from '../../api/client'
import type { MyProvider } from '../../api/types'
import { PasswordField } from '../../components/PasswordField'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Section } from '../../components/ui'
import { oidcErrorKey } from '../../vendor/nexoidc/oidc'

/**
 * Was der Rueckweg vom Anbieter in die Adresse schrieb: `?linked=<kurzname>` oder `?error=<code>`. Einmal gelesen,
 * verschwindet es, damit ein Neuladen die Meldung nicht wiederholt.
 */
function takeReturn(): { linked: string | null; error: string | null } {
  const params = new URLSearchParams(window.location.search)
  const linked = params.get('linked')
  const error = oidcErrorKey(params.get('error'))
  if (!params.has('linked') && !params.has('error')) return { linked: null, error: null }
  params.delete('linked')
  params.delete('error')
  const rest = params.toString()
  window.history.replaceState(window.history.state, '', window.location.pathname + (rest ? `?${rest}` : ''))
  return { linked, error }
}

/**
 * Mein Konto, Sicherheit, Anmeldeanbieter (Bauplan 04): je aktivem Anbieter eine Zeile mit Zustand und "Verknuepfen"
 * oder "Loesen". Verknuepfen fragt das Passwort noch einmal; danach geht es zum Anbieter und zurueck hierher.
 */
export function MyProviders() {
  const { t } = useTranslation()
  const [returned] = useState(() => takeReturn())
  const [rows, setRows] = useState<MyProvider[] | null>(null)
  const [asking, setAsking] = useState<string | null>(null)
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [problem, setProblem] = useState<unknown>(null)

  const load = useCallback(async () => {
    try {
      setRows(await oidcApi.mine())
    } catch (error) {
      setProblem(error)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function link(event: FormEvent, slug: string) {
    event.preventDefault()
    setBusy(slug)
    setProblem(null)
    try {
      const { url } = await oidcApi.linkStart(slug, password)
      // Weiter zum Anbieter; zurueck kommt der Browser auf diese Seite.
      window.location.assign(url)
    } catch (error) {
      setProblem(error)
      setBusy(null)
    }
  }

  async function unlink(slug: string) {
    setBusy(slug)
    setProblem(null)
    try {
      await oidcApi.unlink(slug)
      await load()
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(null)
    }
  }

  // Nach dem Verknuepfen der Name des Anbieters, nie der Kurzname aus der Adresse.
  const linkedName = returned.linked ? (rows?.find((row) => row.slug === returned.linked)?.label ?? null) : null
  if (rows !== null && rows.length === 0 && !returned.error) return null

  return (
    <Section title={t('oidc.account.title')} intro={t('oidc.account.text')}>
      {linkedName && <FormMessage tone="ok">{t('system.signIn.linkedNow', { name: linkedName })}</FormMessage>}
      {returned.error && <FormMessage>{t(returned.error)}</FormMessage>}
      {rows !== null && rows.length > 0 && (
        <ul className="divide-y divide-ink-700 rounded-2xl border border-ink-700" data-testid="my-providers">
          {rows.map((row) => (
            <li key={row.slug} className="flex flex-col gap-3 px-4 py-3 text-sm">
              <div className="flex flex-wrap items-center gap-3">
                <span className="min-w-0 flex-1">
                  <span className="block font-medium text-mist-100 wrap-anywhere">{row.label}</span>
                  {row.linked && row.email && <span className="block text-xs text-mist-500 wrap-anywhere">{row.email}</span>}
                </span>
                <Badge tone={row.linked ? 'ok' : 'neutral'}>{row.linked ? t('oidc.account.linked') : t('oidc.account.notLinked')}</Badge>
                {row.linked ? (
                  <Button variant="ghost" size="sm" loading={busy === row.slug} onClick={() => void unlink(row.slug)}>
                    {t('oidc.account.unlink')}
                  </Button>
                ) : (
                  asking !== row.slug && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        setAsking(row.slug)
                        setPassword('')
                      }}
                    >
                      <Symbol name="link" />
                      {t('oidc.account.link')}
                    </Button>
                  )
                )}
              </div>
              {!row.linked && asking === row.slug && (
                <form onSubmit={(event) => void link(event, row.slug)} className="flex flex-col gap-3 sm:flex-row sm:items-end" noValidate>
                  <div className="min-w-0 flex-1">
                    <PasswordField
                      label={t('system.signIn.linkPassword')}
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      autoComplete="current-password"
                      autoFocus
                    />
                  </div>
                  <Button type="submit" loading={busy === row.slug} disabled={password === ''}>
                    {t('oidc.account.link')}
                  </Button>
                </form>
              )}
            </li>
          ))}
        </ul>
      )}
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
    </Section>
  )
}
