import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { oidcApi } from '../../api/auth'
import { ApiError, errorText } from '../../api/client'
import { systemSettingsApi } from '../../api/outside'
import type { AuthentikResult, OidcProvider, OidcProviderForm, PasswordLogin } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { PasswordField } from '../../components/PasswordField'
import { Symbol } from '../../components/Symbol'
import { buttonClasses } from '../../components/buttonClasses'
import { Badge, Button, Field, FormMessage, Section, Spinner, Switch } from '../../components/ui'
import { withBase } from '../../lib/base'
import {
  authentikIssuerChange,
  authentikReasonKey,
  authentikStepKey,
  oidcErrorKey,
  oidcFormErrorKey,
  slugFromLabel,
} from '../../vendor/nexoidc/oidc'

const BLUEPRINT = withBase('/api/oidc/authentik/blueprint')

/**
 * Einstellungen, System, Anmeldung: drei Karten untereinander wie in jeder nex-App (Bauplan 04). Aufbau, Reihenfolge
 * und Woerter kommen aus dem Bauplan (`oidc.*`), die Bauteile aus nexcrate.
 *
 * 1. Anmeldung: Anmeldung mit Passwort (an, solange kein aktiver Anbieter verknuepft ist), oeffentliche Adresse.
 * 2. Anmeldeanbieter: die Liste mit Marken und Ziehgriff, das Formular als Dialog.
 * 3. authentik in einem Schritt: Adresse, Token, Einrichten, Blueprint, die Schritte mit dem Grund eines Fehlers.
 *
 * nexcrate hat ein Konto (Bauplan 06): "Neue Personen bekommen ein Konto" fehlt im Formular, und der zweite Faktor
 * steht unter Konto.
 */
export function SignInSettings() {
  const [providers, setProviders] = useState<OidcProvider[] | null>(null)
  const [address, setAddress] = useState<string | null>(null)
  const [problem, setProblem] = useState<unknown>(null)

  const load = useCallback(async () => {
    try {
      setProviders(await oidcApi.providers())
    } catch (error) {
      setProblem(error)
      setProviders([])
    }
  }, [])

  useEffect(() => {
    void load()
    systemSettingsApi.read().then(
      (settings) => setAddress(settings.web_url ?? ''),
      () => setAddress(''),
    )
  }, [load])

  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-4">
      <SignInCard address={address} onAddress={setAddress} providers={providers} />
      <ProvidersCard providers={providers} reload={load} address={address ?? ''} />
      <AuthentikCard address={address} reload={load} />
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
    </div>
  )
}

// --- Karte 1: Anmeldung --------------------------------------------------------------------------------------------

function SignInCard({
  address,
  onAddress,
  providers,
}: {
  address: string | null
  onAddress: (next: string) => void
  providers: OidcProvider[] | null
}) {
  const { t } = useTranslation()
  const [state, setState] = useState<PasswordLogin | null>(null)
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [problem, setProblem] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)

  // Neu lesen, wenn sich die Liste aendert: ein entfernter Anbieter oeffnet das Passwort wieder.
  useEffect(() => {
    oidcApi.passwordLogin().then(setState, setProblem)
  }, [providers])

  useEffect(() => {
    if (address !== null) setValue(address)
  }, [address])

  async function switchPassword(enabled: boolean) {
    setBusy('password')
    setProblem(null)
    try {
      setState(await oidcApi.setPasswordLogin(enabled))
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(null)
    }
  }

  async function saveAddress(event: FormEvent) {
    event.preventDefault()
    setBusy('address')
    setProblem(null)
    setSaved(false)
    try {
      const answer = await systemSettingsApi.change({ web_url: value })
      onAddress(answer.web_url ?? '')
      setSaved(true)
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(null)
    }
  }

  return (
    <Section title={t('oidc.admin.signInTitle')}>
      <div id="sign-in" className="flex flex-col gap-4">
        {state === null ? (
          <Spinner />
        ) : (
          <Switch
            label={t('oidc.admin.password')}
            hint={`${t('oidc.admin.passwordHint')} ${t('system.signIn.wayBack')}`}
            checked={state.password_login}
            // Ohne aktiven, verknuepften Anbieter kaeme niemand mehr herein: der Schalter bleibt an.
            disabled={busy !== null || (state.password_login && !state.can_switch_off)}
            onChange={(enabled) => void switchPassword(enabled)}
          />
        )}
        {state !== null && state.password_login && !state.can_switch_off && (
          <p className="text-xs text-mist-500">{t('system.signIn.needsLink')}</p>
        )}
        {state !== null && !state.password_login && <FormMessage tone="info">{t('system.signIn.offNow')}</FormMessage>}
        {state?.emergency_switch && <FormMessage tone="info">{t('system.signIn.emergency')}</FormMessage>}
        <form onSubmit={(event) => void saveAddress(event)} className="flex flex-col gap-3 sm:flex-row sm:items-end" noValidate>
          <div className="min-w-0 flex-1">
            <Field
              label={t('oidc.admin.address')}
              hint={t('oidc.admin.addressHint')}
              value={value}
              onChange={(event) => setValue(event.target.value)}
              placeholder="https://nexcrate.example.com"
              spellCheck={false}
              autoComplete="off"
            />
          </div>
          <Button type="submit" loading={busy === 'address'} className="sm:mb-6">
            {t('common.actions.save')}
          </Button>
        </form>
        {saved && <FormMessage tone="ok">{t('system.outside.saved')}</FormMessage>}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Section>
  )
}

// --- Karte 2: Anmeldeanbieter -------------------------------------------------------------------------------------

type Removal = { entry: OidcProvider; count: number; only: number }

function ProvidersCard({ providers, reload, address }: { providers: OidcProvider[] | null; reload: () => Promise<void>; address: string }) {
  const { t } = useTranslation()
  const [editing, setEditing] = useState<OidcProvider | 'new' | null>(null)
  const [removing, setRemoving] = useState<Removal | null>(null)
  const [dragged, setDragged] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const list = providers ?? []
  const readOnly = list.some((entry) => !entry.editable)

  async function run(action: () => Promise<void>) {
    setBusy(true)
    setProblem(null)
    try {
      await action()
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  function move(from: number, to: number) {
    if (from === to || to < 0 || to >= list.length) return
    const ids = list.map((entry) => entry.id)
    const [taken] = ids.splice(from, 1)
    ids.splice(to, 0, taken)
    void run(async () => {
      await oidcApi.orderProviders(ids)
      await reload()
    })
  }

  return (
    <Section title={t('oidc.admin.providersTitle')} intro={t('oidc.admin.providersText')}>
      {providers === null && <Spinner />}
      {providers !== null && list.length === 0 && <p className="text-sm text-mist-400">{t('oidc.admin.empty')}</p>}
      {list.length > 0 && (
        <ul className="divide-y divide-ink-700 rounded-2xl border border-ink-700" data-testid="provider-list">
          {list.map((entry, index) => (
            <li
              key={entry.id}
              className={'flex flex-wrap items-center gap-3 px-4 py-3 text-sm ' + (dragged === index ? 'opacity-50' : '')}
              draggable={!readOnly && list.length > 1}
              onDragStart={() => setDragged(index)}
              onDragEnd={() => setDragged(null)}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault()
                if (dragged !== null) move(dragged, index)
                setDragged(null)
              }}
            >
              {!readOnly && list.length > 1 && (
                <button
                  type="button"
                  className="cursor-grab rounded p-1 text-mist-500 hover:text-mist-200"
                  aria-label={t('system.signIn.order', { name: entry.label })}
                  onKeyDown={(event) => {
                    if (event.key === 'ArrowUp') move(index, index - 1)
                    if (event.key === 'ArrowDown') move(index, index + 1)
                  }}
                >
                  <Symbol name="grip" />
                </button>
              )}
              <span className="min-w-0 flex-1">
                <span className="block font-medium text-mist-100 wrap-anywhere">{entry.label}</span>
                <span className="block text-xs text-mist-500 wrap-anywhere">{entry.issuer}</span>
              </span>
              <span className="flex flex-wrap items-center gap-1.5">
                <Badge tone={entry.enabled ? 'ok' : 'neutral'}>{entry.enabled ? t('oidc.admin.active') : t('oidc.admin.inactive')}</Badge>
                {entry.managed === 'authentik' && <Badge tone="accent">{t('oidc.admin.managed')}</Badge>}
              </span>
              {entry.editable && (
                <span className="flex gap-2">
                  <Button variant="ghost" size="sm" onClick={() => setEditing(entry)}>
                    {t('oidc.admin.edit')}
                  </Button>
                  <Button
                    variant="danger"
                    size="sm"
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        const impact = await oidcApi.impact(entry.id)
                        setRemoving({ entry, count: impact.count, only: impact.only })
                      })
                    }
                  >
                    {t('oidc.admin.remove')}
                  </Button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {!readOnly && (
        <div>
          <Button onClick={() => setEditing('new')}>
            <Symbol name="plus" />
            {t('oidc.admin.add')}
          </Button>
        </div>
      )}
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      {editing !== null && (
        <ProviderDialog
          entry={editing === 'new' ? null : editing}
          address={address}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null)
            await reload()
          }}
        />
      )}
      <Dialog
        open={removing !== null}
        title={t('oidc.admin.remove')}
        onClose={() => setRemoving(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setRemoving(null)}>
              {t('common.actions.cancel')}
            </Button>
            <Button
              variant="danger"
              loading={busy}
              onClick={() =>
                void run(async () => {
                  if (removing) await oidcApi.removeProvider(removing.entry.id)
                  setRemoving(null)
                  await reload()
                })
              }
            >
              {t('oidc.admin.remove')}
            </Button>
          </>
        }
      >
        <p className="text-sm text-mist-300">
          {removing && t('oidc.admin.removeConfirm', { name: removing.entry.label, count: removing.count, only: removing.only })}
        </p>
      </Dialog>
    </Section>
  )
}

const EMPTY: OidcProviderForm = {
  label: '',
  slug: '',
  issuer: '',
  client_id: '',
  client_secret: '',
  scopes: 'openid profile email',
  enabled: true,
  auto_create: false,
  trusts_second_factor: true,
}

type FieldName = 'label' | 'slug' | 'issuer' | 'client_id' | 'client_secret' | 'scopes' | 'id'

/** Derselbe Aussteller, wie auch immer am Ende geschrieben (`same_issuer` des Servers ohne Platzhalter). */
function sameIssuer(a: string, b: string): boolean {
  return a.trim().replace(/\/+$/, '') === b.trim().replace(/\/+$/, '')
}

/** Das Formular als Dialog, die Felder in der Reihenfolge des Bauplans. */
function ProviderDialog({
  entry,
  address,
  onClose,
  onSaved,
}: {
  entry: OidcProvider | null
  address: string
  onClose: () => void
  onSaved: () => Promise<void>
}) {
  const { t } = useTranslation()
  const [form, setForm] = useState<OidcProviderForm>(
    entry
      ? {
          label: entry.label,
          issuer: entry.issuer,
          client_id: entry.client_id,
          client_secret: '',
          scopes: entry.scopes,
          enabled: entry.enabled,
          auto_create: false,
          trusts_second_factor: entry.trusts_second_factor,
        }
      : EMPTY,
  )
  // Der Kurzname folgt dem Namen, bis er selbst getippt wird.
  const [slugTyped, setSlugTyped] = useState(false)
  const [busy, setBusy] = useState(false)
  const [fieldError, setFieldError] = useState<{ field: FieldName; text: string } | null>(null)
  const [confirmIssuer, setConfirmIssuer] = useState<number | null>(null)
  const [copied, setCopied] = useState(false)

  const slug = entry ? entry.slug : slugTyped ? (form.slug ?? '') : slugFromLabel(form.label)
  const base = (address || window.location.origin + withBase('')).replace(/\/+$/, '')
  const redirect = entry ? entry.redirect_uri : `${base}/api/oidc/${slug || '…'}/callback`
  const set = (change: Partial<OidcProviderForm>) => setForm((current) => ({ ...current, ...change }))

  function errorFor(error: unknown): { field: FieldName; text: string } {
    if (!(error instanceof ApiError)) return { field: 'id', text: errorText(t, error) }
    const field = (typeof error.values.field === 'string' ? error.values.field : 'id') as FieldName
    const key = oidcFormErrorKey(error.code) ?? oidcErrorKey(error.code)
    return { field, text: key ? t(key) : errorText(t, error) }
  }

  async function save() {
    setBusy(true)
    setFieldError(null)
    try {
      const body = { ...form, slug: entry ? undefined : slug }
      if (entry) await oidcApi.saveProvider(entry.id, body)
      else await oidcApi.addProvider(body)
      await onSaved()
    } catch (error) {
      setFieldError(errorFor(error))
    } finally {
      setBusy(false)
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    // Ein anderer Aussteller ist ein anderer Anbieter: erst sagen, wie viele Konten neu verknuepfen muessen.
    if (entry && entry.links > 0 && !sameIssuer(entry.issuer, form.issuer)) {
      try {
        const impact = await oidcApi.impact(entry.id, form.issuer.trim())
        if (impact.issuer_change > 0) {
          setConfirmIssuer(impact.issuer_change)
          return
        }
      } catch (error) {
        setFieldError(errorFor(error))
        return
      }
    }
    await save()
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(redirect)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }

  const problemAt = (field: FieldName) => (fieldError?.field === field ? <FormMessage role="alert">{fieldError.text}</FormMessage> : null)
  const formId = 'oidc-provider-form'

  return (
    <Dialog
      open
      wide
      title={entry ? `${t('oidc.admin.edit')}: ${entry.label}` : t('oidc.admin.add')}
      onClose={() => !busy && onClose()}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button type="submit" form={formId} loading={busy}>
            {t('common.actions.save')}
          </Button>
        </>
      }
    >
      <form id={formId} className="flex flex-col gap-4" onSubmit={(event) => void submit(event)} data-testid="provider-form" noValidate>
        <Field label={t('oidc.admin.label')} value={form.label} onChange={(event) => set({ label: event.target.value })} maxLength={64} />
        {problemAt('label')}
        {entry ? (
          <p className="text-xs text-mist-500">
            {t('oidc.admin.slug')}: <code className="font-mono text-mist-300">{entry.slug}</code>
          </p>
        ) : (
          <>
            <Field
              label={t('oidc.admin.slug')}
              hint={t('oidc.admin.slugHint')}
              value={slug}
              onChange={(event) => {
                setSlugTyped(true)
                set({ slug: event.target.value })
              }}
              spellCheck={false}
              maxLength={40}
            />
            {problemAt('slug')}
          </>
        )}
        <Field
          label={t('oidc.admin.issuer')}
          hint={t('oidc.admin.issuerHint')}
          value={form.issuer}
          onChange={(event) => set({ issuer: event.target.value })}
          placeholder="https://auth.example.com/application/o/nexcrate/"
          spellCheck={false}
        />
        {problemAt('issuer')}
        <Field label={t('oidc.admin.clientId')} value={form.client_id} onChange={(event) => set({ client_id: event.target.value })} spellCheck={false} />
        {problemAt('client_id')}
        <PasswordField
          label={t('oidc.admin.secret')}
          value={form.client_secret}
          onChange={(event) => set({ client_secret: event.target.value })}
          placeholder={entry?.has_secret ? t('oidc.admin.secretKept') : ''}
          autoComplete="new-password"
        />
        {problemAt('client_secret')}
        <div className="flex flex-col gap-1.5">
          <span className="text-sm font-medium text-mist-300">{t('oidc.admin.redirect')}</span>
          <div className="flex flex-wrap items-center gap-2">
            <code className="min-w-0 flex-1 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 font-mono text-xs text-mist-100 wrap-anywhere" data-testid="redirect-uri">
              {redirect}
            </code>
            <Button variant="ghost" size="sm" onClick={() => void copy()}>
              {copied ? t('system.signIn.copied') : t('system.signIn.copy')}
            </Button>
          </div>
        </div>
        <Switch label={t('oidc.admin.enabled')} checked={form.enabled} onChange={(enabled) => set({ enabled })} />
        <Switch
          label={t('oidc.admin.trustsSecondFactor')}
          hint={t('oidc.admin.trustsSecondFactorHint')}
          checked={form.trusts_second_factor}
          onChange={(trusts_second_factor) => set({ trusts_second_factor })}
        />
        <details open={fieldError?.field === 'scopes' || undefined}>
          <summary className="cursor-pointer text-sm text-mist-400">{t('system.signIn.more')}</summary>
          <div className="mt-3">
            <Field label={t('oidc.admin.scopes')} value={form.scopes} onChange={(event) => set({ scopes: event.target.value })} spellCheck={false} />
            {problemAt('scopes')}
          </div>
        </details>
        {problemAt('id')}
      </form>
      <Dialog
        open={confirmIssuer !== null}
        title={t('oidc.admin.issuer')}
        onClose={() => setConfirmIssuer(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmIssuer(null)}>
              {t('common.actions.cancel')}
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                setConfirmIssuer(null)
                void save()
              }}
            >
              {t('common.actions.save')}
            </Button>
          </>
        }
      >
        <p className="text-sm text-mist-300">{t('oidc.admin.issuerChangeConfirm', { count: confirmIssuer ?? 0 })}</p>
      </Dialog>
    </Dialog>
  )
}

// --- Karte 3: authentik in einem Schritt ---------------------------------------------------------------------------

function AuthentikCard({ address, reload }: { address: string | null; reload: () => Promise<void> }) {
  const { t } = useTranslation()
  const [url, setUrl] = useState('')
  const [token, setToken] = useState('')
  const [result, setResult] = useState<AuthentikResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  const [asking, setAsking] = useState<number | null>(null)

  async function runButton(confirmIssuerChange: boolean) {
    setBusy(true)
    setProblem(null)
    setResult(null)
    try {
      const answer = await oidcApi.authentik(url.trim(), token.trim(), confirmIssuerChange)
      setResult(answer)
      // Haelt der Knopf vor einem anderen Aussteller an, bleibt das Token fuer den bestaetigten zweiten Lauf.
      const count = authentikIssuerChange(answer)
      if (count === null) setToken('')
      else setAsking(count)
      await reload()
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  function setUp(event: FormEvent) {
    event.preventDefault()
    void runButton(false)
  }

  return (
    <Section title={t('oidc.authentik.title')} intro={t('oidc.authentik.text')}>
      <div id="sign-in-authentik" className="flex flex-col gap-4">
        {address === '' && (
          <FormMessage tone="info">
            {t('oidc.authentik.noAddress')}{' '}
            <a href="#sign-in" className="underline">
              {t('oidc.admin.address')}
            </a>
          </FormMessage>
        )}
        <form onSubmit={setUp} className="flex flex-col gap-4" noValidate>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              label={t('oidc.authentik.url')}
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="https://auth.example.com"
              autoComplete="off"
              spellCheck={false}
            />
            <PasswordField label={t('oidc.authentik.token')} value={token} onChange={(event) => setToken(event.target.value)} autoComplete="off" />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" loading={busy} disabled={url.trim() === '' || token.trim() === ''}>
              {t('oidc.authentik.run')}
            </Button>
            <a href={BLUEPRINT} download className={buttonClasses('ghost', 'md')}>
              <Symbol name="download" />
              {t('oidc.authentik.blueprint')}
            </a>
          </div>
        </form>
        {busy && (
          <p className="text-sm text-mist-400" role="status">
            {t('oidc.authentik.asking')}
          </p>
        )}
        {result && (
          <ol className="flex flex-col gap-1.5 text-sm" data-testid="authentik-steps">
            {result.steps.map((step) => {
              const name = authentikStepKey(step.key)
              const why = step.ok ? null : authentikReasonKey(step.reason)
              return (
                <li key={step.key} className="flex items-start gap-2">
                  <span className={step.ok ? 'text-ok-500' : 'text-bad-500'}>
                    <Symbol name={step.ok ? 'check' : 'close'} />
                  </span>
                  <span className="min-w-0">
                    <span className={step.ok ? 'text-mist-200' : 'font-medium text-bad-500'}>{name ? t(name) : step.key}</span>
                    {why && <span className="block text-xs text-mist-400">{t(why, { status: step.status ?? 0, count: result.count ?? 0 })}</span>}
                  </span>
                </li>
              )
            })}
          </ol>
        )}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
      <Dialog
        open={asking !== null}
        title={t('oidc.authentik.title')}
        onClose={() => setAsking(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setAsking(null)}>
              {t('common.actions.cancel')}
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                setAsking(null)
                void runButton(true)
              }}
            >
              {t('oidc.authentik.run')}
            </Button>
          </>
        }
      >
        <p className="text-sm text-mist-300">{t('oidc.admin.issuerChangeConfirm', { count: asking ?? 0 })}</p>
      </Dialog>
    </Section>
  )
}
