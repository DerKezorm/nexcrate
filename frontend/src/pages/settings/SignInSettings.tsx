import { useCallback, useEffect, useState, type FormEvent } from 'react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'

import { oidcApi } from '../../api/auth'
import { ApiError, errorText } from '../../api/client'
import type { AuthentikStep, OidcConfig } from '../../api/types'
import { PasswordField } from '../../components/PasswordField'
import { Symbol } from '../../components/Symbol'
import { buttonClasses } from '../../components/buttonClasses'
import { Badge, Button, Field, FormMessage, Section, Spinner, Switch } from '../../components/ui'
import { withBase } from '../../lib/base'
import { formatDate } from '../../lib/format'

const BLUEPRINT = withBase('/api/oidc/authentik/blueprint')

/**
 * Was der Rueckweg vom Anbieter in die Adresse schrieb (`?oidc=linked` oder `?oidc_error=<code>`). Einmal gelesen,
 * verschwindet es, damit ein Neuladen die Meldung nicht wiederholt.
 */
function takeReturn(): { linked: boolean; error: string | null } {
  const params = new URLSearchParams(window.location.search)
  const linked = params.get('oidc') === 'linked'
  const raw = params.get('oidc_error')
  const error = raw && /^[a-z0-9_]+$/.test(raw) ? raw : null
  if (!linked && !raw) return { linked, error }
  params.delete('oidc')
  params.delete('oidc_error')
  const rest = params.toString()
  window.history.replaceState(window.history.state, '', window.location.pathname + (rest ? `?${rest}` : ''))
  return { linked, error }
}

/** Die Schritte des authentik-Knopfs in Worten der Oberflaeche; was sie nicht kennt, steht mit seinem Namen da. */
function stepLabel(t: TFunction, key: string): string {
  const labels: Record<string, string> = {
    reached: t('system.account.signIn.steps.reached'),
    owner: t('system.account.signIn.steps.owner'),
    signing_key: t('system.account.signIn.steps.signing_key'),
    mappings: t('system.account.signIn.steps.mappings'),
    provider: t('system.account.signIn.steps.provider'),
    application: t('system.account.signIn.steps.application'),
    binding: t('system.account.signIn.steps.binding'),
    filled: t('system.account.signIn.steps.filled'),
  }
  return labels[key] ?? key
}

/**
 * System, Konto: Anmeldung ueber einen Anbieter wie authentik. Drei Stufen: einrichten (authentik-Knopf, Blueprint
 * oder von Hand), das eine Konto beim Anbieter verknuepfen, dann wahlweise die Anmeldung mit Passwort ausschalten.
 */
export function SignInSettings({ onPasswordLogin }: { onPasswordLogin?: (on: boolean) => void } = {}) {
  const { t, i18n } = useTranslation()
  const [config, setConfig] = useState<OidcConfig | null>(null)
  const [problem, setProblem] = useState<unknown>(null)
  const [returned] = useState(() => takeReturn())
  const [busy, setBusy] = useState<string | null>(null)

  const [url, setUrl] = useState('')
  const [token, setToken] = useState('')
  const [steps, setSteps] = useState<AuthentikStep[] | null>(null)
  const [owner, setOwner] = useState('')

  const [byHand, setByHand] = useState(false)
  const [form, setForm] = useState({ issuer: '', client_id: '', client_secret: '', provider_name: '' })

  const [linkPassword, setLinkPassword] = useState('')
  const [copied, setCopied] = useState(false)

  const load = useCallback(async () => {
    try {
      const answer = await oidcApi.config()
      setConfig(answer)
      setForm((current) => ({ ...current, issuer: answer.issuer, client_id: answer.client_id, provider_name: answer.provider_name }))
    } catch (error) {
      setProblem(error)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // Die Abschnitte Passwort und zweiter Faktor sagen dazu, wenn sie nur noch fuer den Notzugang gelten.
  useEffect(() => {
    if (config) onPasswordLogin?.(config.password_login)
  }, [config, onPasswordLogin])

  async function run(name: string, action: () => Promise<void>) {
    setBusy(name)
    setProblem(null)
    try {
      await action()
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(null)
    }
  }

  function setupAuthentik(event: FormEvent) {
    event.preventDefault()
    void run('authentik', async () => {
      const answer = await oidcApi.authentik(url.trim(), token.trim())
      setToken('')
      setSteps(answer.steps)
      setOwner(answer.owner)
      setConfig(answer.provider)
    })
  }

  function saveByHand(event: FormEvent) {
    event.preventDefault()
    void run('hand', async () => {
      setConfig(await oidcApi.save(form))
      setForm((current) => ({ ...current, client_secret: '' }))
      setByHand(false)
    })
  }

  function startLink(event: FormEvent) {
    event.preventDefault()
    void run('link', async () => {
      const { url: target } = await oidcApi.linkStart(linkPassword)
      // Weiter zum Anbieter; zurueck kommt der Browser auf diese Seite.
      window.location.assign(target)
    })
  }

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }

  const name = config?.provider_name || 'OpenID Connect'

  return (
    <Section
      title={t('system.account.signIn.title')}
      intro={t('system.account.signIn.intro')}
      actions={config?.linked ? <Badge tone="ok">{t('system.account.signIn.linkedBadge')}</Badge> : undefined}
    >
      {returned.linked && <FormMessage tone="ok">{t('system.account.signIn.linkedNow')}</FormMessage>}
      {returned.error && <FormMessage>{errorText(t, new ApiError(0, returned.error))}</FormMessage>}
      {config === null && problem === null && (
        <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
          <Spinner />
          {t('common.loading')}
        </p>
      )}

      {config && !config.configured && (
        <>
          <form onSubmit={setupAuthentik} className="flex flex-col gap-4 rounded-2xl border border-ink-700 bg-ink-900/60 p-4" noValidate>
            <div>
              <h3 className="font-semibold text-mist-100">{t('system.account.signIn.authentik.title')}</h3>
              <p className="mt-1 text-sm text-mist-500">{t('system.account.signIn.authentik.intro')}</p>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field
                label={t('system.account.signIn.authentik.url')}
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                placeholder="https://auth.example.com"
                autoComplete="off"
                spellCheck={false}
              />
              <PasswordField
                label={t('system.account.signIn.authentik.token')}
                hint={t('system.account.signIn.authentik.tokenHint')}
                value={token}
                onChange={(event) => setToken(event.target.value)}
                autoComplete="off"
              />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" loading={busy === 'authentik'} disabled={url.trim() === '' || token.trim() === ''}>
                {t('system.account.signIn.authentik.run')}
              </Button>
              <a href={BLUEPRINT} download className={buttonClasses('ghost', 'md')}>
                <Symbol name="download" />
                {t('system.account.signIn.authentik.blueprint')}
              </a>
            </div>
            <p className="text-xs text-mist-500">{t('system.account.signIn.authentik.blueprintHint')}</p>
          </form>
          <div>
            <Button variant="ghost" onClick={() => setByHand((open) => !open)} aria-expanded={byHand}>
              <Symbol name={byHand ? 'chevronDown' : 'chevron'} />
              {t('system.account.signIn.byHand.toggle')}
            </Button>
          </div>
        </>
      )}

      {steps && (
        <ol className="flex flex-col gap-1.5 text-sm" aria-label={t('system.account.signIn.authentik.stepsLabel')}>
          {steps.map((step) => (
            <li key={step.key} className="flex items-start gap-2">
              <span className={step.ok ? 'text-ok-500' : 'text-bad-500'}>
                <Symbol name={step.ok ? 'check' : 'alert'} />
              </span>
              <span className="min-w-0">
                <span className="font-medium text-mist-200">{stepLabel(t, step.key)}</span>
                <span className="block text-xs text-mist-500 wrap-anywhere">{step.detail}</span>
              </span>
            </li>
          ))}
        </ol>
      )}
      {steps && steps.every((step) => step.ok) && owner && <FormMessage tone="ok">{t('system.account.signIn.authentik.done', { owner })}</FormMessage>}

      {config && byHand && (
        <form onSubmit={saveByHand} className="flex flex-col gap-4 rounded-2xl border border-ink-700 bg-ink-900/60 p-4" noValidate>
          <p className="text-sm text-mist-500">{t('system.account.signIn.byHand.intro')}</p>
          <p className="text-sm text-mist-500">{t('system.account.signIn.byHand.entraHint')}</p>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              label={t('system.account.signIn.byHand.issuer')}
              hint={t('system.account.signIn.byHand.issuerHint')}
              value={form.issuer}
              onChange={(event) => setForm({ ...form, issuer: event.target.value })}
              placeholder="https://auth.example.com/application/o/nexcrate/"
              className="sm:col-span-2"
              spellCheck={false}
            />
            <Field label={t('system.account.signIn.byHand.clientId')} value={form.client_id} onChange={(event) => setForm({ ...form, client_id: event.target.value })} spellCheck={false} />
            <PasswordField
              label={t('system.account.signIn.byHand.clientSecret')}
              hint={config.configured ? t('system.account.signIn.byHand.secretKept') : undefined}
              value={form.client_secret}
              onChange={(event) => setForm({ ...form, client_secret: event.target.value })}
              autoComplete="new-password"
            />
            <Field
              label={t('system.account.signIn.byHand.name')}
              hint={t('system.account.signIn.byHand.nameHint')}
              value={form.provider_name}
              onChange={(event) => setForm({ ...form, provider_name: event.target.value })}
              placeholder="authentik"
            />
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" loading={busy === 'hand'} disabled={form.issuer.trim() === '' || form.client_id.trim() === ''}>
              {t('common.actions.save')}
            </Button>
            <Button variant="ghost" onClick={() => setByHand(false)}>
              {t('common.actions.cancel')}
            </Button>
          </div>
        </form>
      )}

      {config && (
        <div className="flex flex-col gap-1 text-sm">
          <span className="text-mist-400">{t('system.account.signIn.redirect')}</span>
          <div className="flex flex-wrap items-center gap-2">
            <code className="rounded-lg border border-ink-700 bg-ink-900/60 px-2 py-1 font-mono text-xs text-mist-100 wrap-anywhere">{config.redirect_uri}</code>
            <Button variant="ghost" size="sm" onClick={() => void copy(config.redirect_uri)}>
              {copied ? t('system.account.signIn.copied') : t('system.account.signIn.copy')}
            </Button>
          </div>
          <span className="text-xs text-mist-500">{t('system.account.signIn.redirectHint')}</span>
        </div>
      )}

      {config?.configured && (
        <div className="flex flex-col gap-4 rounded-2xl border border-ink-700 bg-ink-900/60 p-4">
          <p className="text-sm text-mist-400">{t('system.account.signIn.configured', { name, issuer: config.issuer })}</p>
          {config.linked ? (
            <p className="text-sm text-mist-200">
              {t('system.account.signIn.linked', {
                name: config.linked_name || t('system.account.signIn.noName'),
                date: config.linked_at ? formatDate(config.linked_at, i18n.language) : '',
              })}
            </p>
          ) : (
            <FormMessage tone="info">{t('system.account.signIn.notLinked', { name })}</FormMessage>
          )}
          <form onSubmit={startLink} className="flex max-w-xl flex-col gap-3 sm:flex-row sm:items-end" noValidate>
            <div className="min-w-0 flex-1">
              <PasswordField
                label={t('system.account.signIn.linkPassword')}
                value={linkPassword}
                onChange={(event) => setLinkPassword(event.target.value)}
                autoComplete="current-password"
              />
            </div>
            <Button type="submit" loading={busy === 'link'} disabled={linkPassword === ''}>
              <Symbol name="link" />
              {config.linked ? t('system.account.signIn.relink', { name }) : t('system.account.signIn.link', { name })}
            </Button>
          </form>

          {config.linked && (
            <Switch
              label={t('system.account.signIn.passwordLogin')}
              hint={t('system.account.signIn.passwordLoginHint')}
              checked={config.password_login}
              disabled={busy !== null}
              onChange={(enabled) => void run('password', async () => setConfig(await oidcApi.passwordLogin(enabled)))}
            />
          )}
          {config.emergency_switch && <FormMessage tone="info">{t('system.account.signIn.emergency')}</FormMessage>}

          <div className="flex flex-wrap gap-2">
            {!byHand && (
              <Button variant="ghost" onClick={() => setByHand(true)}>
                {t('system.account.signIn.byHand.edit')}
              </Button>
            )}
            {config.linked && (
              <Button variant="ghost" loading={busy === 'unlink'} onClick={() => void run('unlink', async () => { await oidcApi.unlink(); await load() })}>
                {t('system.account.signIn.unlink')}
              </Button>
            )}
            <Button
              variant="danger"
              loading={busy === 'remove'}
              onClick={() =>
                void run('remove', async () => {
                  await oidcApi.remove()
                  setSteps(null)
                  await load()
                })
              }
            >
              {t('system.account.signIn.remove')}
            </Button>
          </div>
        </div>
      )}

      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
    </Section>
  )
}
