import { api } from './client'

/** Die sieben Dienste, in der Reihenfolge der Reiter. */
export type NotifyChannel = 'ntfy' | 'gotify' | 'telegram' | 'discord' | 'webhook' | 'apprise' | 'email'

export type NotifyLevel = 'low' | 'normal' | 'high' | 'urgent'

/** Was die Oberflaeche von einem Dienst wissen muss: Felder je Ebene, Geheimnisse, Pflichtfelder, Code. */
export type NotifyService = {
  channel: NotifyChannel
  label: string
  parent_fields: string[]
  child_fields: string[]
  parent_required: string[]
  child_required: string[]
  secrets: string[]
  requires_code: boolean
  chats: boolean
}

export type NotifyOverview = {
  services: NotifyService[]
  groups: { group: string; events: string[] }[]
  levels: NotifyLevel[]
}

/** Eine Kachel. Geheimnisse kommen nie zurueck, nur ob eines gespeichert ist. */
export type NotifyTarget = {
  id: number
  channel: NotifyChannel
  parent_id: number | null
  name: string
  enabled: boolean
  verified: boolean
  events: Record<string, NotifyLevel>
  fields: Record<string, string>
  secrets_set: Record<string, boolean>
  last_error: { code: string | null; params: Record<string, unknown>; at: string | null } | null
  last_delivered_at: string | null
  created_at: string
  children: NotifyTarget[]
}

export type NotifyDraft = { fields: Record<string, string>; target_id?: number | null; parent_id?: number | null }

export type NotifySave = { name: string; fields: Record<string, string>; events: Record<string, NotifyLevel>; parent_id?: number | null }

export type TelegramChat = { chat_id: string; name: string; type: string }

const base = (channel: NotifyChannel) => `/notifications/${channel}`

export const notifyApi = {
  overview: () => api.get<NotifyOverview>('/notifications'),
  targets: (channel: NotifyChannel) => api.get<NotifyTarget[]>(`${base(channel)}/targets`),
  /** Prueft eine Instanz, schickt nichts. Telegram nennt dabei den Namen des Bots. */
  check: (channel: NotifyChannel, draft: NotifyDraft) => api.post<{ found: Record<string, string> }>(`${base(channel)}/check`, draft),
  /** Eine Testnachricht mit vierstelligem Code; bei E-Mail genuegt die angenommene Testmail. */
  test: (channel: NotifyChannel, draft: NotifyDraft) => api.post<{ requires_code: boolean; confirmed: boolean }>(`${base(channel)}/test`, draft),
  confirmCode: (channel: NotifyChannel, code: string) => api.post<{ requires_code: boolean; confirmed: boolean }>(`${base(channel)}/confirm`, { code }),
  chats: (channel: NotifyChannel, draft: NotifyDraft) => api.post<{ chats: TelegramChat[] }>(`${base(channel)}/chats`, draft),
  create: (channel: NotifyChannel, body: NotifySave) => api.post<NotifyTarget>(`${base(channel)}/targets`, body),
  change: (channel: NotifyChannel, id: number, body: NotifySave) => api.put<NotifyTarget>(`${base(channel)}/targets/${id}`, body),
  setEnabled: (channel: NotifyChannel, id: number, enabled: boolean) => api.put<NotifyTarget>(`${base(channel)}/targets/${id}/enabled`, { enabled }),
  remove: (channel: NotifyChannel, id: number) => api.delete<void>(`${base(channel)}/targets/${id}`),
}
