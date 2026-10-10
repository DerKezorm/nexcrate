import { ApiError } from '../api/client'

/**
 * Die Rueckfrage, bevor eine neue oeffentliche Adresse gespeichert wird (Bauplan 04, Karte 1 und 3): Sie liesse eine
 * Rueckleitadresse zurueck, die authentik (`count`, die vom Knopf eingerichteten) oder ein von Hand eingetragener
 * Anbieter (`hand`) kennt. Der Server antwortet dann 409 `public_url_change` und speichert nichts.
 */
export type AddressQuestion = { count: number; address: string; hand: number }

/** Die Rueckfrage aus der Fehlerantwort, oder null, wenn der Fehler eine andere Ursache hat. */
export function addressQuestion(error: unknown): AddressQuestion | null {
  if (!(error instanceof ApiError) || error.code !== 'public_url_change') return null
  const { count, address, hand } = error.values
  return { count: Number(count) || 0, address: typeof address === 'string' ? address : '', hand: Number(hand) || 0 }
}
