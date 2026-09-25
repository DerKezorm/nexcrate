/** Grenzen wie im Server: Passwort mindestens 8 Zeichen, Benutzername hoechstens 64. */
export const PASSWORD_MIN = 8
export const USERNAME_MAX = 64

/** Zeichen gezaehlt wie in Python: Ein Emoji ist eines, nicht zwei. */
export function characterCount(text: string): number {
  return [...text].length
}
