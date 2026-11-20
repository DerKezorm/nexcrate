import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { musicApi } from '../../api/music'
import type { GenresChange, GenresState } from '../../api/types'
import { Button, FormMessage, SelectField, Switch } from '../../components/ui'
import { formatNumber } from '../../lib/format'

/** Wert der Auswahl fuer "faellt weg"; die Schnittstelle nennt ihn genauso. */
const DROP = 'drop'

/** Eine Antwort ohne Listen (aelterer Server) zeigt den Abschnitt nicht, statt die Seite zu kippen. */
function complete(result: GenresState | null | undefined): result is GenresState {
  return (
    !!result && Array.isArray(result.targets) && Array.isArray(result.choices) && Array.isArray(result.unmapped)
  )
}

/**
 * "Genres vereinheitlichen": ab Werk aus. An schreibt nexcrate beim Ablegen und ueber "Tags pruefen" eines von
 * wenigen Genres statt der Schreibweise des Downloads. Darunter die Namen (englisch oder mit deutschen Namen),
 * die Regel fuer Deutschrap, Schreibweisen ohne Zuordnung und die eigenen Zuordnungen. Gespeichert wird sofort.
 */
export function MusicGenres() {
  const { t, i18n } = useTranslation()
  const [state, setState] = useState<GenresState | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  useEffect(() => {
    const abort = new AbortController()
    musicApi.genres(abort.signal).then(
      (result) => {
        if (!abort.signal.aborted && complete(result)) setState(result)
      },
      (error: unknown) => {
        if (!abort.signal.aborted) setLoadError(error)
      },
    )
    return () => abort.abort()
  }, [])

  async function change(body: GenresChange) {
    setBusy(true)
    setProblem(null)
    try {
      const result = await musicApi.changeGenres(body)
      if (complete(result)) setState(result)
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  if (state === null) return loadError !== null ? <FormMessage>{errorText(t, loadError)}</FormMessage> : null
  const nameOf = (key: string) =>
    key === DROP ? t('music.genres.drop') : (state.targets.find((target) => target.key === key)?.name ?? key)
  const options = (
    <>
      {state.targets.map((target) => (
        <option key={target.key} value={target.key}>
          {target.name}
        </option>
      ))}
      <option value={DROP}>{t('music.genres.drop')}</option>
    </>
  )

  return (
    <div className="flex flex-col gap-3">
      <Switch
        label={t('music.genres.enabled')}
        hint={t('music.genres.enabledHint')}
        checked={state.enabled}
        onChange={(next) => void change({ enabled: next })}
        disabled={busy}
      />
      {state.enabled && (
        <div className="flex flex-col gap-3 border-l border-ink-700 pl-4">
          <SelectField
            label={t('music.genres.names')}
            value={state.names}
            onChange={(event) => void change({ names: event.target.value as GenresState['names'] })}
            disabled={busy}
          >
            <option value="en">{t('music.genres.namesEn')}</option>
            <option value="de">{t('music.genres.namesDe')}</option>
          </SelectField>
          <Switch
            label={t('music.genres.regional')}
            hint={t('music.genres.regionalHint')}
            checked={state.regional}
            onChange={(next) => void change({ regional: next })}
            disabled={busy}
          />
          {state.unmapped.length > 0 && (
            <div className="flex flex-col gap-2">
              <p className="text-sm font-medium text-mist-300">{t('music.genres.unmapped')}</p>
              <p className="text-xs text-mist-500">{t('music.genres.unmappedHint')}</p>
              <ul className="flex flex-col gap-2">
                {state.unmapped.map((item) => (
                  <li key={item.name} className="grid grid-cols-[minmax(0,1fr)_auto] items-end gap-3">
                    <SelectField
                      label={t('music.genres.spelling', {
                        name: item.name,
                        count: item.count,
                        value: formatNumber(item.count, i18n.language),
                      })}
                      value=""
                      onChange={(event) =>
                        void change({ choices: [{ spelling: item.name, target: event.target.value }] })
                      }
                      disabled={busy}
                    >
                      <option value="" disabled>
                        {t('music.genres.choose')}
                      </option>
                      {options}
                    </SelectField>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {state.choices.length > 0 && (
            <div className="flex flex-col gap-2">
              <p className="text-sm font-medium text-mist-300">{t('music.genres.choices')}</p>
              <ul className="flex flex-col gap-1">
                {state.choices.map((choice) => (
                  <li key={choice.spelling} className="flex flex-wrap items-center gap-2 text-sm text-mist-300">
                    <span className="min-w-0 break-words">
                      {t('music.genres.choice', { spelling: choice.spelling, genre: nameOf(choice.target) })}
                    </span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => void change({ forget: [choice.spelling] })}
                      disabled={busy}
                    >
                      {t('music.genres.forget')}
                    </Button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
    </div>
  )
}
