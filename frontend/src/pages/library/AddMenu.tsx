import { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { Symbol, type SymbolName } from '../../components/Symbol'
import { Button } from '../../components/ui'

/** The address of the folder page, "Ordner einlesen". */
export const DISK_PATH = '/ordner'
/** Die Seite Ordner mit der Art Serien (S6, P2). */
export const SERIES_DISK_PATH = '/ordner?art=serien'

type MenuItem = { label: string; symbol: SymbolName; action: () => void }

/**
 * "Hinzufügen" as a small menu. Escape and a click elsewhere close it; the arrow keys walk the items.
 *
 * Film: "Film suchen" and "Ordner einlesen". Serie: nur "Serie suchen", Ordner einlesen gibt es bisher nur fuer
 * Filme. Musik (M1.5): "Künstler hinzufügen" und "Album hinzufügen", `onSearch` bleibt dafuer ungenutzt.
 */
export function AddMenu({
  onSearch,
  kind = 'movie',
  onAddArtist,
  onAddAlbum,
}: {
  onSearch: () => void
  kind?: 'movie' | 'series' | 'music'
  /** Nur bei `kind="music"`. */
  onAddArtist?: () => void
  /** Nur bei `kind="music"`. */
  onAddAlbum?: () => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const menuId = useId()
  const wrapper = useRef<HTMLDivElement>(null)
  const items = useRef<(HTMLButtonElement | null)[]>([])

  const series = kind === 'series'
  const menuItems: MenuItem[] =
    kind === 'music'
      ? [
          { label: t('music.add.artist'), symbol: 'search', action: () => onAddArtist?.() },
          { label: t('music.add.album'), symbol: 'note', action: () => onAddAlbum?.() },
        ]
      : [
          { label: series ? t('series.add.menu') : t('library.add.movie'), symbol: 'search', action: onSearch },
          { label: series ? t('series.add.disk') : t('library.add.disk'), symbol: 'folder', action: () => navigate(series ? SERIES_DISK_PATH : DISK_PATH) },
        ]
  // ⚠️ Fuer Serien zaehlen die Pfeiltasten nur den ersten Eintrag: Ordner einlesen gab es dort erst spaeter dazu,
  // die Tastaturschleife blieb bewusst bei einem Eintrag (wie vor Musik M1).
  const itemCount = kind === 'music' ? menuItems.length : series ? 1 : menuItems.length
  const menuLabel = kind === 'music' ? t('music.add.menuLabel') : series ? t('series.add.menuLabel') : t('library.add.menuLabel')
  // Der Knopf sagt, was er hinzufuegt: er steht neben der Suche, weit weg von der Ueberschrift.
  const buttonLabel = kind === 'music' ? t('music.add.open') : series ? t('series.add.open') : t('library.add.openMovie')

  useEffect(() => {
    if (!open) return
    function onPointer(event: MouseEvent) {
      if (wrapper.current && !wrapper.current.contains(event.target as Node)) setOpen(false)
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onPointer)
    document.addEventListener('keydown', onKey)
    items.current[0]?.focus()
    return () => {
      document.removeEventListener('mousedown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  function onMenuKey(event: React.KeyboardEvent) {
    const focused = items.current.slice(0, itemCount).findIndex((item) => item === document.activeElement)
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      const step = event.key === 'ArrowDown' ? 1 : -1
      const next = (focused + step + itemCount) % itemCount
      items.current[next]?.focus()
    }
  }

  function choose(action: () => void) {
    setOpen(false)
    action()
  }

  const itemClass = 'flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left text-sm text-mist-200 hover:bg-ink-800 hover:text-mist-100 focus:bg-ink-800 focus:outline-none'

  return (
    <div ref={wrapper} className="relative">
      <Button onClick={() => setOpen((value) => !value)} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? menuId : undefined} aria-label={menuLabel}>
        <Symbol name="plus" />
        {buttonLabel}
        <Symbol name="chevronDown" className="h-3.5 w-3.5" />
      </Button>
      {open && (
        <div
          id={menuId}
          role="menu"
          aria-label={buttonLabel}
          onKeyDown={onMenuKey}
          className="absolute right-0 z-30 mt-2 flex w-56 flex-col gap-0.5 rounded-2xl border border-ink-700 bg-ink-850 p-1.5 shadow-2xl shadow-black/50"
        >
          {menuItems.map((item, index) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              ref={(element) => {
                items.current[index] = element
              }}
              onClick={() => choose(item.action)}
              className={itemClass}
            >
              <Symbol name={item.symbol} className="h-4 w-4 text-accent-400" />
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
