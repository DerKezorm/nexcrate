import { useCallback, useEffect, useId, useState, type FormEvent } from 'react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'

import { ApiError, errorText } from '../../api/client'
import {
  notifyApi,
  type NotifyChannel,
  type NotifyLevel,
  type NotifyOverview,
  type NotifyService,
  type NotifyTarget,
  type TelegramChat,
} from '../../api/notifications'
import appriseLogo from '../../assets/notify/apprise.svg?inline'
import discordLogo from '../../assets/notify/discord.svg?inline'
import emailLogo from '../../assets/notify/email.svg?inline'
import gotifyLogo from '../../assets/notify/gotify.svg?inline'
import ntfyLogo from '../../assets/notify/ntfy.svg?inline'
import telegramLogo from '../../assets/notify/telegram.svg?inline'
import webhookLogo from '../../assets/notify/webhook.svg?inline'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Button, Field, FormMessage, Section, SelectField, Spinner } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatDateTime } from '../../lib/format'

/**
 * Einstellungen, "Benachrichtigungen", gebaut wie Nexviews Systembenachrichtigungen:
 * oben die Dienste, darunter die Kacheln des gewaehlten Dienstes und eine Plus-Kachel. Das Formular steht unter der
 * Kachel, die anderen treten solange zurueck. Manche Dienste haben zwei Ebenen: Bei ntfy traegt die Instanz Adresse und
 * Anmeldung, das Topic darunter ist das Postfach; ebenso Bot und Chat bei Telegram, Mailserver und Adresse bei E-Mail.
 *
 * Ein Postfach wird erst gespeichert, wenn seine Testnachricht angekommen ist: den vierstelligen Code eintippen, bei
 * E-Mail genuegt die angenommene Testmail. Geheimnisse kommen vom Server nie zurueck, ein leeres Feld laesst sie stehen.
 */

const CHANNELS: NotifyChannel[] = ['ntfy', 'gotify', 'telegram', 'discord', 'webhook', 'apprise', 'email']

const LOGOS: Record<NotifyChannel, string> = {
  ntfy: ntfyLogo,
  gotify: gotifyLogo,
  telegram: telegramLogo,
  discord: discordLogo,
  webhook: webhookLogo,
  apprise: appriseLogo,
  email: emailLogo,
}

/** Einfarbig als Maske: passt zu Bernstein und zum dunklen wie hellen Thema. */
function ServiceLogo({ channel, className = 'h-4 w-4' }: { channel: NotifyChannel; className?: string }) {
  const mask = `url("${LOGOS[channel]}")`
  return (
    <span
      aria-hidden="true"
      className={'inline-block shrink-0 bg-current ' + className}
      style={{ maskImage: mask, WebkitMaskImage: mask, maskSize: 'contain', WebkitMaskSize: 'contain', maskRepeat: 'no-repeat', WebkitMaskRepeat: 'no-repeat', maskPosition: 'center', WebkitMaskPosition: 'center' }}
    />
  )
}

type Editing = { target: NotifyTarget | null; parent: NotifyTarget | null }

export function NotificationSettings() {
  const { t } = useTranslation()
  const notice = useNotice()
  const [overview, setOverview] = useState<NotifyOverview | null>(null)
  const [channel, setChannel] = useState<NotifyChannel>('ntfy')
  const [tiles, setTiles] = useState<NotifyTarget[] | null>(null)
  const [problem, setProblem] = useState<unknown>(null)
  const [editing, setEditing] = useState<Editing | null>(null)
  const [removing, setRemoving] = useState<NotifyTarget | null>(null)

  useEffect(() => {
    notifyApi.overview().then(setOverview, setProblem)
  }, [])

  const load = useCallback(() => {
    notifyApi.targets(channel).then(
      (found) => {
        setTiles(found)
        // Was offen ist, bekommt den frischen Stand, etwa ein neues Topic unter der Instanz.
        setEditing((current) => {
          if (current === null) return null
          const upper = current.parent ?? current.target
          const fresh = upper !== null ? (found.find((tile) => tile.id === upper.id) ?? null) : null
          if (current.parent !== null) return { target: current.target, parent: fresh }
          return { target: fresh ?? current.target, parent: null }
        })
      },
      setProblem,
    )
  }, [channel])

  useEffect(load, [load])

  const service = overview?.services.find((item) => item.channel === channel) ?? null
  const twoLevels = service !== null && service.child_fields.length > 0

  async function act(action: () => Promise<unknown>, done: string) {
    setProblem(null)
    try {
      await action()
      notice(done)
      load()
    } catch (error) {
      setProblem(error)
    }
  }

  function pick(next: NotifyChannel) {
    setChannel(next)
    setTiles(null)
    setEditing(null)
    setProblem(null)
  }

  const upper = editing === null ? null : (editing.parent ?? editing.target)
  const shown = tiles === null ? [] : editing === null ? tiles : tiles.filter((tile) => upper !== null && tile.id === upper.id)

  return (
    <div className="flex flex-col gap-4">
      <Section title={t('notify.title')} intro={t('notify.intro')}>
        <div className="flex flex-wrap gap-2" role="tablist" aria-label={t('notify.title')}>
          {CHANNELS.map((item) => (
            <button
              key={item}
              type="button"
              role="tab"
              aria-selected={item === channel}
              onClick={() => pick(item)}
              className={
                'inline-flex items-center gap-2 rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors ' +
                (item === channel ? 'border-accent-500/60 bg-accent-500/15 text-accent-400' : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100')
              }
            >
              <ServiceLogo channel={item} />
              {overview?.services.find((entry) => entry.channel === item)?.label ?? item}
            </button>
          ))}
        </div>
        <p className="text-sm text-mist-500">{serviceIntro(t, channel)}</p>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
        {tiles === null || overview === null || service === null ? (
          problem === null && (
            <p className="flex items-center gap-2 py-4 text-sm text-mist-500" role="status">
              <Spinner />
              {t('common.loading')}
            </p>
          )
        ) : (
          <>
            {tiles.length === 0 && editing === null && <p className="text-sm text-mist-500">{t('notify.empty')}</p>}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {shown.map((tile) => (
                <TargetTile
                  key={tile.id}
                  target={tile}
                  active={editing !== null}
                  twoLevels={twoLevels}
                  onOpen={() => setEditing(editing !== null ? null : { target: tile, parent: null })}
                  onToggle={(enabled) => void act(() => notifyApi.setEnabled(channel, tile.id, enabled), t(enabled ? 'notify.on' : 'notify.off', { name: tile.name }))}
                  onRemove={() => setRemoving(tile)}
                />
              ))}
              {editing === null && <PlusTile channel={channel} label={addLabel(t, channel, false)} onClick={() => setEditing({ target: null, parent: null })} />}
            </div>
            {editing !== null && editing.parent === null && (
              <TargetForm
                key={`upper-${editing.target?.id ?? 'new'}`}
                service={service}
                overview={overview}
                target={editing.target}
                parent={null}
                onCancel={() => setEditing(null)}
                onSaved={(saved) => {
                  notice(t('notify.saved', { name: saved.name }))
                  // Eine neue Instanz bleibt offen: darunter kommen gleich die Postfaecher.
                  setEditing(twoLevels ? { target: saved, parent: null } : null)
                  load()
                }}
              />
            )}
            {editing !== null && twoLevels && upper !== null && (
              <div className="flex flex-col gap-3 border-l-2 border-accent-500/40 pl-4">
                <h3 className="text-sm font-semibold text-mist-200">{childrenTitle(t, channel)}</h3>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  {upper.children
                    .filter((child) => editing.parent === null || editing.target === null || editing.target.id === child.id)
                    .map((child) => (
                      <TargetTile
                        key={child.id}
                        target={child}
                        active={editing.parent !== null}
                        twoLevels={false}
                        onOpen={() => setEditing(editing.parent !== null ? { target: upper, parent: null } : { target: child, parent: upper })}
                        onToggle={(enabled) => void act(() => notifyApi.setEnabled(channel, child.id, enabled), t(enabled ? 'notify.on' : 'notify.off', { name: child.name }))}
                        onRemove={() => setRemoving(child)}
                      />
                    ))}
                  {editing.parent === null && <PlusTile channel={channel} label={addLabel(t, channel, true)} onClick={() => setEditing({ target: null, parent: upper })} />}
                </div>
                {editing.parent !== null && (
                  <TargetForm
                    key={`child-${editing.target?.id ?? 'new'}`}
                    service={service}
                    overview={overview}
                    target={editing.target}
                    parent={editing.parent}
                    onCancel={() => setEditing({ target: editing.parent, parent: null })}
                    onSaved={(saved) => {
                      notice(t('notify.saved', { name: saved.name }))
                      setEditing({ target: editing.parent, parent: null })
                      load()
                    }}
                  />
                )}
              </div>
            )}
          </>
        )}
      </Section>
      {removing !== null && (
        <Dialog
          open
          title={t('notify.removeTitle', { name: removing.name })}
          onClose={() => setRemoving(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setRemoving(null)}>
                {t('notify.cancel')}
              </Button>
              <Button
                variant="danger"
                onClick={() => {
                  const gone = removing
                  setRemoving(null)
                  setEditing(null)
                  void act(() => notifyApi.remove(channel, gone.id), t('notify.removed', { name: gone.name }))
                }}
              >
                <Symbol name="trash" />
                {t('common.actions.remove')}
              </Button>
            </>
          }
        >
          <p className="text-sm text-mist-300">{t('notify.removeText')}</p>
        </Dialog>
      )}
    </div>
  )
}

function serviceIntro(t: TFunction, channel: NotifyChannel): string {
  const texts: Record<NotifyChannel, string> = {
    ntfy: t('notify.services.ntfy'),
    gotify: t('notify.services.gotify'),
    telegram: t('notify.services.telegram'),
    discord: t('notify.services.discord'),
    webhook: t('notify.services.webhook'),
    apprise: t('notify.services.apprise'),
    email: t('notify.services.email'),
  }
  return texts[channel]
}

function addLabel(t: TFunction, channel: NotifyChannel, child: boolean): string {
  if (child) {
    if (channel === 'ntfy') return t('notify.add.ntfyChild')
    if (channel === 'telegram') return t('notify.add.telegramChild')
    return t('notify.add.emailChild')
  }
  const texts: Record<NotifyChannel, string> = {
    ntfy: t('notify.add.ntfy'),
    gotify: t('notify.add.gotify'),
    telegram: t('notify.add.telegram'),
    discord: t('notify.add.discord'),
    webhook: t('notify.add.webhook'),
    apprise: t('notify.add.apprise'),
    email: t('notify.add.email'),
  }
  return texts[channel]
}

function childrenTitle(t: TFunction, channel: NotifyChannel): string {
  if (channel === 'ntfy') return t('notify.children.ntfy')
  if (channel === 'telegram') return t('notify.children.telegram')
  return t('notify.children.email')
}

// --- Kacheln ------------------------------------------------------------------------------------------------------ //

function TargetTile({
  target,
  active,
  twoLevels,
  onOpen,
  onToggle,
  onRemove,
}: {
  target: NotifyTarget
  active: boolean
  twoLevels: boolean
  onOpen: () => void
  onToggle: (enabled: boolean) => void
  onRemove: () => void
}) {
  const { t, i18n } = useTranslation()
  const eventCount = Object.keys(target.events).length
  const sub = twoLevels ? t('notify.tile.children', { count: target.children.length }) : t('notify.tile.events', { count: eventCount })
  let status: { text: string; bad: boolean }
  if (target.last_error !== null) {
    const error = errorText(t, new ApiError(200, target.last_error.code ?? 'internal_error', target.last_error.params))
    status = { text: t('notify.tile.failing', { error }), bad: true }
  } else if (!twoLevels && !target.verified) {
    status = { text: t('notify.tile.unconfirmed'), bad: true }
  } else if (target.last_delivered_at !== null) {
    status = { text: t('notify.tile.delivered', { when: formatDateTime(target.last_delivered_at, i18n.language) }), bad: false }
  } else {
    status = { text: twoLevels ? '' : t('notify.tile.never'), bad: false }
  }
  return (
    <div
      className={
        'relative flex min-h-28 flex-col gap-2 overflow-hidden rounded-xl border p-4 transition-colors ' +
        (active ? 'border-accent-500/60 bg-accent-500/10 ' : 'border-ink-700 bg-ink-900/60 hover:border-ink-600 ') +
        (target.enabled ? '' : 'opacity-60')
      }
    >
      <ServiceLogo channel={target.channel} className="pointer-events-none absolute -right-3 -bottom-3 h-20 w-20 text-mist-100 opacity-5" />
      <button type="button" onClick={onOpen} className="text-left">
        <span className="block text-base font-semibold wrap-anywhere text-mist-100">{target.name}</span>
        <span className="block text-xs text-mist-500">{sub}</span>
      </button>
      <div className="mt-auto flex items-end justify-between gap-3">
        <p className={'min-w-0 text-xs ' + (status.bad ? 'text-bad-400' : 'text-mist-500')}>{status.text}</p>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            role="switch"
            aria-checked={target.enabled}
            aria-label={t('notify.tile.switch', { name: target.name })}
            onClick={() => onToggle(!target.enabled)}
            className={'relative inline-flex h-6 w-11 items-center rounded-full border transition-colors ' + (target.enabled ? 'border-accent-500 bg-accent-500' : 'border-ink-600 bg-ink-700')}
          >
            <span aria-hidden="true" className={'inline-block h-4 w-4 rounded-full transition-transform ' + (target.enabled ? 'translate-x-6 bg-ink-900' : 'translate-x-1 bg-mist-500')} />
          </button>
          <button
            type="button"
            aria-label={t('notify.tile.remove', { name: target.name })}
            onClick={onRemove}
            className="inline-flex h-8 w-8 items-center justify-center rounded-full text-mist-500 hover:bg-bad-500/15 hover:text-bad-400"
          >
            <Symbol name="trash" />
          </button>
        </div>
      </div>
    </div>
  )
}

function PlusTile({ channel, label, onClick }: { channel: NotifyChannel; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex min-h-28 flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-ink-600 p-4 text-sm text-mist-500 transition-colors hover:border-accent-500/60 hover:text-accent-400"
    >
      <span className="inline-flex items-center gap-2">
        <Symbol name="plus" />
        <ServiceLogo channel={channel} />
      </span>
      {label}
    </button>
  )
}

// --- Formular ------------------------------------------------------------------------------------------------------ //

type Confirmation = 'none' | 'sent' | 'confirmed'

function TargetForm({
  service,
  overview,
  target,
  parent,
  onCancel,
  onSaved,
}: {
  service: NotifyService
  overview: NotifyOverview
  target: NotifyTarget | null
  parent: NotifyTarget | null
  onCancel: () => void
  onSaved: (saved: NotifyTarget) => void
}) {
  const { t } = useTranslation()
  const channel = service.channel
  const child = parent !== null || (target !== null && target.parent_id !== null)
  const mailbox = child || service.child_fields.length === 0
  const levelFields = child ? service.child_fields : service.parent_fields
  const [name, setName] = useState(target?.name ?? '')
  const [fields, setFields] = useState<Record<string, string>>(() => initialFields(levelFields, target))
  const [events, setEvents] = useState<Record<string, NotifyLevel>>(target?.events ?? {})
  const [confirmation, setConfirmation] = useState<Confirmation>('none')
  const [code, setCode] = useState('')
  const [chats, setChats] = useState<TelegramChat[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const [info, setInfo] = useState<string | null>(null)

  function set(field: string, value: string) {
    setFields((current) => ({ ...current, [field]: value }))
    // Eine geaenderte Verbindung braucht eine neue Testnachricht.
    if (confirmation !== 'none') {
      setConfirmation('none')
      setInfo(null)
    }
  }

  const draft = { fields, target_id: target?.id ?? null, parent_id: parent?.id ?? null }

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

  function sendTest() {
    void run(async () => {
      const sent = await notifyApi.test(channel, draft)
      setConfirmation(sent.confirmed ? 'confirmed' : 'sent')
      setInfo(sent.confirmed ? t('notify.mailAccepted') : t('notify.testSent'))
    })
  }

  function confirmCode() {
    void run(async () => {
      await notifyApi.confirmCode(channel, code)
      setConfirmation('confirmed')
      setInfo(t('notify.confirmed'))
    })
  }

  function loadChats() {
    void run(async () => {
      const found = await notifyApi.chats(channel, { fields: {}, parent_id: parent?.id ?? null })
      setChats(found.chats)
    })
  }

  function save(event: FormEvent) {
    event.preventDefault()
    void run(async () => {
      let sent = fields
      if (channel === 'telegram' && !child) {
        // Der Name des Bots kommt von Telegram selbst, niemand muss ihn abtippen.
        const checked = await notifyApi.check(channel, draft)
        if (checked.found.username) sent = { ...fields, username: checked.found.username }
      }
      const body = { name, fields: sent, events: mailbox ? events : {}, parent_id: parent?.id ?? null }
      const saved = target === null ? await notifyApi.create(channel, body) : await notifyApi.change(channel, target.id, body)
      onSaved(saved)
    })
  }

  const visible = levelFields.filter((field) => shownField(field, fields))

  return (
    <form onSubmit={save} className="flex flex-col gap-4 rounded-xl border border-accent-500/40 bg-ink-900/60 p-4">
      <Field label={t('notify.fields.name')} hint={t('notify.fields.nameHint')} value={name} onChange={(event) => setName(event.target.value)} required maxLength={100} />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {visible.map((field) => (
          <FieldInput key={field} service={service} field={field} value={fields[field] ?? ''} stored={target?.secrets_set[field] ?? false} onChange={(value) => set(field, value)} />
        ))}
      </div>
      {channel === 'telegram' && child && (
        <div className="flex flex-col gap-2">
          <Button variant="ghost" size="sm" className="self-start" onClick={loadChats} loading={busy}>
            {t('notify.chats')}
          </Button>
          {chats !== null && chats.length === 0 && <p className="text-xs text-mist-500">{t('notify.chatsNone')}</p>}
          {chats !== null && chats.length > 0 && (
            <ul className="flex flex-col gap-1">
              {chats.map((chat) => (
                <li key={chat.chat_id} className="flex items-center justify-between gap-2 rounded-lg bg-ink-850 px-3 py-1.5 text-sm">
                  <span className="min-w-0 wrap-anywhere text-mist-200">
                    {chat.name} <span className="text-xs text-mist-500">{chat.chat_id}</span>
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      set('chat_id', chat.chat_id)
                      if (name === '') setName(chat.name)
                    }}
                  >
                    {t('notify.chatPick')}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {mailbox && (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-end gap-2">
            <Button variant="ghost" onClick={sendTest} loading={busy}>
              <Symbol name="bell" />
              {t('notify.test')}
            </Button>
            {confirmation === 'sent' && (
              <>
                <Field label={t('notify.code')} value={code} onChange={(event) => setCode(event.target.value)} inputMode="numeric" autoComplete="one-time-code" maxLength={4} className="w-40" />
                <Button onClick={confirmCode} loading={busy} disabled={code.trim().length !== 4}>
                  {t('notify.confirm')}
                </Button>
              </>
            )}
          </div>
          {info !== null && (
            <FormMessage tone={confirmation === 'confirmed' ? 'ok' : 'info'} role="status">
              {info}
            </FormMessage>
          )}
        </div>
      )}
      {mailbox && <EventChoice overview={overview} events={events} onChange={setEvents} />}
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="ghost" onClick={onCancel} disabled={busy}>
          {t('notify.cancel')}
        </Button>
        <Button type="submit" loading={busy}>
          {mailbox ? t('notify.save') : t('notify.saveCheck')}
        </Button>
      </div>
    </form>
  )
}

function initialFields(levelFields: string[], target: NotifyTarget | null): Record<string, string> {
  const found: Record<string, string> = {}
  for (const field of levelFields) found[field] = target?.fields[field] ?? DEFAULTS[field] ?? ''
  return found
}

const DEFAULTS: Record<string, string> = { auth: 'none', smtp_port: '587', smtp_security: 'starttls', language: 'de' }

/** Bei ntfy zeigt die Art der Anmeldung nur die Felder, die sie braucht. */
function shownField(field: string, fields: Record<string, string>): boolean {
  if (!('auth' in fields)) return true
  if (field === 'username' || field === 'password') return fields.auth === 'basic'
  if (field === 'token') return fields.auth === 'token'
  return true
}

function FieldInput({
  service,
  field,
  value,
  stored,
  onChange,
}: {
  service: NotifyService
  field: string
  value: string
  stored: boolean
  onChange: (value: string) => void
}) {
  const { t } = useTranslation()
  const label = fieldLabel(t, service.channel, field)
  const hint = fieldHint(t, field)
  if (field === 'auth') {
    return (
      <SelectField label={label} value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="none">{t('notify.options.authNone')}</option>
        <option value="basic">{t('notify.options.authBasic')}</option>
        <option value="token">{t('notify.options.authToken')}</option>
      </SelectField>
    )
  }
  if (field === 'smtp_security') {
    return (
      <SelectField label={label} value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="starttls">{t('notify.options.securityStarttls')}</option>
        <option value="ssl">{t('notify.options.securitySsl')}</option>
        <option value="none">{t('notify.options.securityNone')}</option>
      </SelectField>
    )
  }
  if (field === 'language') {
    return (
      <SelectField label={label} value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="de">{t('notify.options.languageDe')}</option>
        <option value="en">{t('notify.options.languageEn')}</option>
      </SelectField>
    )
  }
  if (field === 'silent') {
    return <CheckField label={label} checked={value === 'on'} onChange={(checked) => onChange(checked ? 'on' : '')} />
  }
  const secret = service.secrets.includes(field)
  return (
    <Field
      label={label}
      hint={secret && stored ? t('notify.fields.secretKept') : hint}
      type={secret ? 'password' : field === 'address' || field === 'smtp_from_address' ? 'email' : 'text'}
      autoComplete={secret ? 'new-password' : 'off'}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      maxLength={1024}
    />
  )
}

function CheckField({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  const id = useId()
  return (
    <div className="flex items-center gap-2 self-end pb-2">
      <input id={id} type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="h-4 w-4 accent-accent-500" />
      <label htmlFor={id} className="text-sm text-mist-200">
        {label}
      </label>
    </div>
  )
}

function fieldLabel(t: TFunction, channel: NotifyChannel, field: string): string {
  if (field === 'url') {
    if (channel === 'ntfy') return t('notify.fields.urlNtfy')
    if (channel === 'discord') return t('notify.fields.urlDiscord')
    if (channel === 'apprise') return t('notify.fields.urlApprise')
    return t('notify.fields.url')
  }
  if (field === 'token') {
    if (channel === 'gotify') return t('notify.fields.tokenGotify')
    if (channel === 'telegram') return t('notify.fields.tokenTelegram')
    if (channel === 'webhook') return t('notify.fields.tokenWebhook')
    return t('notify.fields.token')
  }
  if (field === 'topic') return channel === 'apprise' ? t('notify.fields.topicApprise') : t('notify.fields.topic')
  if (field === 'username' && channel === 'discord') return t('notify.fields.discordName')
  const texts: Record<string, string> = {
    auth: t('notify.fields.auth'),
    username: t('notify.fields.username'),
    password: t('notify.fields.password'),
    chat_id: t('notify.fields.chat_id'),
    thread_id: t('notify.fields.thread_id'),
    silent: t('notify.fields.silent'),
    address: t('notify.fields.address'),
    subject: t('notify.fields.subject'),
    smtp_host: t('notify.fields.smtp_host'),
    smtp_port: t('notify.fields.smtp_port'),
    smtp_security: t('notify.fields.smtp_security'),
    smtp_from_address: t('notify.fields.smtp_from_address'),
    smtp_from_name: t('notify.fields.smtp_from_name'),
    language: t('notify.fields.language'),
  }
  return texts[field] ?? field
}

function fieldHint(t: TFunction, field: string): string | undefined {
  if (field === 'thread_id') return t('notify.fields.thread_idHint')
  if (field === 'subject') return t('notify.fields.subjectHint')
  return undefined
}

// --- Ereignisse ------------------------------------------------------------------------------------------------------ //

function EventChoice({
  overview,
  events,
  onChange,
}: {
  overview: NotifyOverview
  events: Record<string, NotifyLevel>
  onChange: (events: Record<string, NotifyLevel>) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-3">
      <div>
        <h3 className="text-sm font-semibold text-mist-200">{t('notify.eventsTitle')}</h3>
        <p className="text-xs text-mist-500">{t('notify.eventsHint')}</p>
      </div>
      {overview.groups.map((group) => (
        <fieldset key={group.group} className="flex flex-col gap-1.5">
          <legend className="mb-1 text-xs font-semibold tracking-wide text-mist-500 uppercase">{groupLabel(t, group.group)}</legend>
          {group.events.map((key) => (
            <EventRow
              key={key}
              label={eventLabel(t, key)}
              level={events[key] ?? null}
              levels={overview.levels}
              onChange={(level) => {
                const next = { ...events }
                if (level === null) delete next[key]
                else next[key] = level
                onChange(next)
              }}
            />
          ))}
        </fieldset>
      ))}
    </div>
  )
}

function EventRow({ label, level, levels, onChange }: { label: string; level: NotifyLevel | null; levels: NotifyLevel[]; onChange: (level: NotifyLevel | null) => void }) {
  const { t } = useTranslation()
  const id = useId()
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg px-2 py-1 hover:bg-ink-850">
      <div className="flex items-center gap-2">
        <input id={id} type="checkbox" checked={level !== null} onChange={(event) => onChange(event.target.checked ? 'normal' : null)} className="h-4 w-4 accent-accent-500" />
        <label htmlFor={id} className="text-sm text-mist-200">
          {label}
        </label>
      </div>
      <select
        aria-label={t('notify.levelOf', { event: label })}
        value={level ?? 'normal'}
        disabled={level === null}
        onChange={(event) => onChange(event.target.value as NotifyLevel)}
        className="rounded-lg border border-ink-700 bg-ink-900 px-2 py-1 text-xs text-mist-200 disabled:opacity-40"
      >
        {levels.map((item) => (
          <option key={item} value={item}>
            {levelLabel(t, item)}
          </option>
        ))}
      </select>
    </div>
  )
}

function groupLabel(t: TFunction, group: string): string {
  const texts: Record<string, string> = {
    loading: t('notify.groups.loading'),
    attention: t('notify.groups.attention'),
    library: t('notify.groups.library'),
    operation: t('notify.groups.operation'),
  }
  return texts[group] ?? group
}

function eventLabel(t: TFunction, key: string): string {
  const texts: Record<string, string> = {
    download_started: t('notify.events.download_started'),
    download_imported: t('notify.events.download_imported'),
    download_upgraded: t('notify.events.download_upgraded'),
    download_failed: t('notify.events.download_failed'),
    problem_opened: t('notify.events.problem_opened'),
    title_added: t('notify.events.title_added'),
    title_removed: t('notify.events.title_removed'),
    file_deleted: t('notify.events.file_deleted'),
    season_complete: t('notify.events.season_complete'),
    request_made: t('notify.events.request_made'),
    health_changed: t('notify.events.health_changed'),
    update_available: t('notify.events.update_available'),
  }
  return texts[key] ?? key
}

function levelLabel(t: TFunction, level: NotifyLevel): string {
  const texts: Record<NotifyLevel, string> = {
    low: t('notify.levels.low'),
    normal: t('notify.levels.normal'),
    high: t('notify.levels.high'),
    urgent: t('notify.levels.urgent'),
  }
  return texts[level]
}

