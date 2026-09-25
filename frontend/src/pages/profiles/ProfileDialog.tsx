import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { profileFileUrl, profilesApi } from '../../api/profiles'
import type { ProfileBrief } from '../../api/types'
import { buttonClasses } from '../../components/buttonClasses'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Spinner } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { ExpertPanel } from './ExpertPanel'
import { ImportDialog } from './ImportDialog'
import { ProfileWizard } from './ProfileWizard'

type Part = 'wizard' | 'expert'

const INPUT = 'rounded-lg border border-ink-700 bg-ink-900 px-2.5 py-1.5 text-sm text-mist-100 focus:border-accent-500 focus:outline-none'

/**
 * Ein Profil an einem Ort (Entscheidung vom 20.09.2026): Name, und darunter entweder der **Assistent** mit
 * seinen Fragen oder **Von Hand** mit Qualitaeten, Cutoff und Punkten. Dazu Exportieren und Importieren.
 *
 * ⚠️ Beide Wege fuehren auf dasselbe Profil. Der Assistent baut es neu; was von Hand gesetzt war, ist danach
 * weg. Das Fenster sagt es, bevor man dort speichert.
 *
 * Groessen je Qualitaet und die Custom Formats stehen nicht hier: sie gelten fuer alle Profile einer Art und
 * haben ihre eigenen Unterreiter.
 */
export function ProfileDialog({ profile, onClose, onDone }: { profile: ProfileBrief; onClose: () => void; onDone: () => void }) {
  const { t } = useTranslation()
  const notify = useNotice()
  const [part, setPart] = useState<Part>(profile.mode === 'expert' || profile.empty === true ? 'expert' : 'wizard')
  const [name, setName] = useState(profile.name)
  const [saved, setSaved] = useState(profile.name)
  const [importing, setImporting] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setName(profile.name)
    setSaved(profile.name)
  }, [profile.name])

  async function rename() {
    if (busy || name.trim() === '' || name.trim() === saved) return
    setBusy(true)
    setProblem(null)
    try {
      const row = await profilesApi.rename(profile.id, name.trim())
      setSaved(row.name)
      notify(t('profiles.dialog.renamed', { name: row.name }))
      onDone()
    } catch (error: unknown) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  const parts: { value: Part; label: string }[] = [
    { value: 'wizard', label: t('profiles.dialog.wizard') },
    { value: 'expert', label: t('profiles.dialog.expert') },
  ]

  return (
    <Dialog open wide title={t('profiles.dialog.title', { name: saved })} onClose={onClose}>
      <div className="flex flex-col gap-4">
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}

        <div className="flex flex-wrap items-end justify-between gap-3">
          <label className="flex min-w-0 flex-1 flex-col gap-1.5 text-sm font-medium text-mist-300">
            {t('profiles.dialog.name')}
            <span className="flex flex-wrap items-center gap-2">
              <input value={name} maxLength={200} onChange={(event) => setName(event.target.value)} className={INPUT + ' min-w-0 flex-1'} />
              <Button size="sm" variant="ghost" onClick={() => void rename()} disabled={busy || name.trim() === '' || name.trim() === saved}>
                {t('profiles.dialog.saveName')}
              </Button>
            </span>
          </label>
          <div className="flex flex-wrap items-center gap-1.5">
            {profile.used_by.length > 0 && <Badge tone="info">{t('profiles.dialog.usedBy', { versions: profile.used_by.join(', ') })}</Badge>}
            {profile.empty !== true && (
              <a href={profileFileUrl(profile.id)} download className={buttonClasses('ghost', 'sm')}>
                <Symbol name="download" className="h-3.5 w-3.5" />
                {t('profiles.tile.export')}
              </a>
            )}
            <Button size="sm" variant="ghost" onClick={() => setImporting(true)}>
              <Symbol name="import" className="h-3.5 w-3.5" />
              {t('profiles.tile.import')}
            </Button>
          </div>
        </div>

        {/* Zwei Wege auf dasselbe Profil; welcher gilt, sagt der Hinweis darunter. */}
        <div className="inline-flex w-fit rounded-full border border-ink-700 bg-ink-850 p-1" role="group" aria-label={t('profiles.dialog.how')}>
          {parts.map((entry) => (
            <button
              key={entry.value}
              type="button"
              aria-pressed={part === entry.value}
              onClick={() => setPart(entry.value)}
              className={
                'rounded-full px-4 py-1.5 text-sm font-semibold transition-colors ' +
                (part === entry.value ? 'bg-accent-500 text-on-accent' : 'text-mist-500 hover:text-mist-300')
              }
            >
              {entry.label}
            </button>
          ))}
        </div>

        {part === 'wizard' ? (
          profile.mode === 'expert' ? (
            <FormMessage tone="bad">{t('profiles.wizard.byHandWarning')}</FormMessage>
          ) : null
        ) : (
          <p className="text-xs text-mist-500">{t('profiles.dialog.expertHint')}</p>
        )}

        {part === 'expert' ? (
          <ExpertPanel profileId={profile.id} onDone={onDone} />
        ) : (
          <ProfileWizardPanel profile={profile} onDone={onDone} onClose={onClose} />
        )}

        {importing && (
          <ImportDialog
            profileId={profile.id}
            kind={profile.kind}
            label={saved}
            filled={profile.empty !== true}
            onClose={() => setImporting(false)}
            onDone={() => {
              setImporting(false)
              onDone()
            }}
          />
        )}
      </div>
    </Dialog>
  )
}

/** Der Assistent im Fenster: er bringt sein eigenes Fenster mit, deshalb steht hier nur der Weg dorthin. */
function ProfileWizardPanel({ profile, onDone, onClose }: { profile: ProfileBrief; onDone: () => void; onClose: () => void }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-mist-400">{t('profiles.dialog.wizardIntro')}</p>
      <div>
        <Button
          onClick={() => {
            setLoading(true)
            setOpen(true)
          }}
          loading={loading}
        >
          {profile.empty === true ? t('profiles.dialog.wizardStart') : t('profiles.dialog.wizardOpen')}
        </Button>
      </div>
      {open && (
        <ProfileWizard
          target={{ kind: profile.kind, label: profile.name, profileId: profile.id, filled: profile.empty !== true }}
          onClose={() => {
            setOpen(false)
            setLoading(false)
          }}
          onDone={() => {
            setOpen(false)
            setLoading(false)
            onDone()
            onClose()
          }}
        />
      )}
      {loading && !open && <Spinner />}
    </div>
  )
}
