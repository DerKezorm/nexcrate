import { useEffect, useState } from 'react'

/** Ganze Sekunden bis `until` (Zeitpunkt in ms), aufgerundet, nie unter null. */
export function secondsUntil(until: number): number {
  return Math.max(0, Math.ceil((until - Date.now()) / 1000))
}

/**
 * Sekunden bis `until`, neu gezeichnet, solange noch Zeit ist. Gerechnet wird mit
 * der Uhr, nicht mit gezaehlten Ticks: Ein Tab im Hintergrund bremst Intervalle,
 * die Anzeige stimmt danach trotzdem.
 */
export function useCountdown(until: number | null): number {
  const [, setTick] = useState(0)

  useEffect(() => {
    if (until === null) return
    const timer = window.setInterval(() => {
      setTick((tick) => tick + 1)
      if (secondsUntil(until) === 0) window.clearInterval(timer)
    }, 250)
    return () => window.clearInterval(timer)
  }, [until])

  return until === null ? 0 : secondsUntil(until)
}
