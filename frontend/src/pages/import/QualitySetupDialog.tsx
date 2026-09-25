import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { sourcesApi } from '../../api/sources'
import type { ArrQualitySetup, Source } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Badge, Button, FormMessage, Spinner } from '../../components/ui'
import { useNotice } from '../../components/useNotice'

/**
 * Die Qualitaets-Einstellungen einer Verbindung uebernehmen (E5): Qualitaetsprofile,
 * Custom Formats und die Groessen je Qualitaet. Das Fenster liest beim Oeffnen und zeigt, was daraus wuerde;
 * uebernommen wird erst auf Knopfdruck.
 *
 * ⚠️ Was nexcrate nicht nachbauen kann, steht dabei: eine Qualitaet, die es nicht kennt, und eine Bedingung, fuer
 * die es keine Art hat. Nichts wird geraten, und in die andere App wird nie geschrieben.
 */
export function QualitySetupDialog({ source, onClose }: { source: Source; onClose: () => void }) {
  const { t } = useTranslation()
  const notify = useNotice()
  const [setup, setSetup] = useState<ArrQualitySetup | null>(null)
  const [chosen, setChosen] = useState<Set<string>>(() => new Set())
  // Vorgewaehlt genau dann, wenn die andere App Repacks von sich aus nimmt: dann aendert sich nichts.
  const [addRepack, setAddRepack] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  async function read() {
    setBusy(true)
    setProblem(null)
    try {
      const read = await sourcesApi.qualitySetup(source.id)
      setSetup(read)
      setAddRepack(read.repacks_upgrade_there)
      setChosen(new Set(read.profiles.filter((profile) => !profile.taken).map((profile) => profile.name)))
    } catch (error: unknown) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    let dropped = false
    sourcesApi
      .qualitySetup(source.id)
      .then((read) => {
        if (dropped) return
        setSetup(read)
        setAddRepack(read.repacks_upgrade_there)
        setChosen(new Set(read.profiles.filter((profile) => !profile.taken).map((profile) => profile.name)))
      })
      .catch((error: unknown) => {
        if (!dropped) setProblem(error)
      })
    return () => {
      dropped = true
    }
  }, [source.id])

  async function take() {
    if (busy || setup === null) return
    setBusy(true)
    setProblem(null)
    try {
      const offered = setup.repack_offer.length > 0 && addRepack
      const result = await sourcesApi.takeQualitySetup(source.id, [...chosen], offered)
      notify(t('import.quality.taken', { profiles: result.profiles, formats: result.formats, sizes: result.sizes }))
      await read()
    } catch (error: unknown) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  function toggle(name: string) {
    setChosen((before) => {
      const next = new Set(before)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const appName = source.app === 'sonarr' ? 'Sonarr' : 'Radarr'
  const newFormats = setup === null ? 0 : setup.formats.filter((format) => !format.exists).length
  const lost = setup === null ? [] : setup.formats.filter((format) => format.unknown_types.length > 0)

  return (
    <Dialog
      open
      wide
      title={t('import.quality.title')}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('import.quality.close')}
          </Button>
          <Button onClick={() => void take()} disabled={setup === null || busy || chosen.size === 0} loading={busy}>
            {t('import.quality.take_all', { count: chosen.size })}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm text-mist-400">{t('import.quality.intro', { name: source.name })}</p>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
        {setup === null ? (
          problem === null && (
            <p className="flex items-center gap-2 text-sm text-mist-500">
              <Spinner /> {t('import.quality.loading')}
            </p>
          )
        ) : (
          <>
            <p className="text-sm text-mist-400">{t('import.quality.found', { profiles: setup.profiles.length, formats: newFormats, sizes: setup.sizes })}</p>

            <ul className="flex flex-col gap-2">
              {setup.profiles.map((profile) => (
                <li key={profile.name} className="flex flex-wrap items-center gap-2 rounded-xl border border-ink-700 bg-ink-900/60 px-3 py-2">
                  <label className="flex min-w-0 flex-1 items-center gap-2">
                    <input
                      type="checkbox"
                      checked={chosen.has(profile.name)}
                      disabled={profile.taken}
                      onChange={() => toggle(profile.name)}
                      className="accent-accent-500"
                      aria-label={t('import.quality.take', { name: profile.name })}
                    />
                    <span className="min-w-0 wrap-anywhere text-mist-100">{profile.name}</span>
                    {profile.taken && <Badge tone="neutral">{t('import.quality.alreadyHere')}</Badge>}
                    {profile.notes.includes('qualities_unknown') && <Badge tone="bad">{t('import.quality.qualitiesUnknown')}</Badge>}
                    {profile.notes.includes('nothing_allowed') && <Badge tone="bad">{t('import.quality.nothingAllowed')}</Badge>}
                    {profile.notes.includes('language_unknown') && <Badge tone="info">{t('import.quality.languageUnknown')}</Badge>}
                    {profile.notes.includes('repack_unscored') && <Badge tone="info">{t('import.quality.repackUnscored')}</Badge>}
                  </label>
                  <span className="text-xs text-mist-500">
                    {t('import.quality.line', { qualities: profile.qualities, cutoff: profile.cutoff ?? '—', formats: profile.scored_formats })}
                  </span>
                </li>
              ))}
            </ul>

            {setup.repack_offer.length > 0 && (
              <div className="flex flex-col gap-2 rounded-xl border border-ink-700 bg-ink-900/60 px-3 py-2">
                <p className="text-sm text-mist-300">{t(setup.repacks_upgrade_there ? 'import.quality.repack.whyThere' : 'import.quality.repack.whyOff')}</p>
                <label className="flex items-center gap-2 text-sm text-mist-100">
                  <input type="checkbox" checked={addRepack} onChange={(event) => setAddRepack(event.target.checked)} className="accent-accent-500" />
                  {t('import.quality.repack.add', { formats: setup.repack_offer.map((offer) => `${offer.name} ${offer.score}`).join(', ') })}
                </label>
              </div>
            )}

            {setup.release_profiles.length > 0 && (
              <section className="flex flex-col gap-2" aria-label={t('import.quality.release.title')}>
                <h3 className="text-sm font-semibold text-mist-100">{t('import.quality.release.title')}</h3>
                <p className="text-xs text-mist-500">{t('import.quality.release.intro')}</p>
                <ul className="flex flex-col gap-2">
                  {setup.release_profiles.map((profile) => (
                    <li key={profile.name} className="flex flex-col gap-1 rounded-xl border border-ink-700 bg-ink-900/60 px-3 py-2">
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="min-w-0 wrap-anywhere text-mist-100">{profile.name}</span>
                        {!profile.enabled && <Badge tone="neutral">{t('import.quality.release.off')}</Badge>}
                        {profile.bound.length > 0 && <Badge tone="bad">{t('import.quality.release.bound')}</Badge>}
                      </span>
                      <span className="text-xs text-mist-500">
                        {!profile.enabled
                          ? t('import.quality.release.offLine')
                          : profile.formats.length === 0
                            ? t('import.quality.release.empty')
                            : t(profile.bound.length > 0 ? 'import.quality.release.boundLine' : 'import.quality.release.line', { formats: profile.formats.join(', ') })}
                      </span>
                      {profile.unreadable_terms.length > 0 && (
                        <span className="text-xs text-mist-500">{t('import.quality.release.unreadable', { terms: profile.unreadable_terms.join(', ') })}</span>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {setup.unknown_qualities.length > 0 && <FormMessage tone="info">{t('import.quality.unknownQualities', { qualities: setup.unknown_qualities.join(', ') })}</FormMessage>}
            {lost.length > 0 && <FormMessage tone="info">{t('import.quality.lostConditions', { formats: lost.map((format) => format.name).join(', ') })}</FormMessage>}
            <section className="flex flex-col gap-1" aria-label={t('import.quality.differs.title')}>
              <h3 className="text-sm font-semibold text-mist-100">{t('import.quality.differs.title')}</h3>
              <ul className="flex list-disc flex-col gap-1 pl-5 text-xs text-mist-400">
                <li>{t('import.quality.differs.notAllowed', { app: appName })}</li>
                <li>{t('import.quality.differs.delay')}</li>
                <li>{t('import.quality.differs.tags')}</li>
              </ul>
            </section>
            <p className="text-xs text-mist-500">{t('import.quality.whereTo')}</p>
          </>
        )}
      </div>
    </Dialog>
  )
}
