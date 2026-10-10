import { Navigate, Route, Routes, useLocation } from 'react-router-dom'

import { AuthGate } from './auth/AuthGate'
import { AppShell } from './components/AppShell'
import { DiskPage } from './pages/disk/DiskPage'
import { DiscoverPage } from './pages/DiscoverPage'
import { CalendarPage } from './pages/CalendarPage'
import { DownloadsPage } from './pages/DownloadsPage'
import { LibraryPage } from './pages/LibraryPage'
import { AboutPage } from './pages/AboutPage'
import { OpenPage } from './pages/OpenPage'
import { ArtistPage } from './pages/music/ArtistPage'
import { SettingsPage } from './pages/SettingsPage'
import { ACCOUNT_TAB_PATH, IMPORT_TAB_PATH } from './pages/settings/tabs'
import { TitlePage } from './pages/TitlePage'

/**
 * Erst Einrichtung oder Anmeldung, dann die App. Deutsche Woerter in der Adresse, wie in Nexview.
 * Die fruehere Seite "Uebernahme" ist heute der Reiter Import der Einstellungen; alte
 * Lesezeichen kommen dort an.
 */
function ToAccount() {
  const { search } = useLocation()
  const extra = new URLSearchParams(search).toString()
  return <Navigate to={ACCOUNT_TAB_PATH + (extra ? `&${extra}` : '')} replace />
}

export default function App() {
  return (
    <AuthGate>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<LibraryPage />} />
          <Route path="titel/:id" element={<TitlePage />} />
          <Route path="kuenstler/:id" element={<ArtistPage />} />
          <Route path="ordner" element={<DiskPage />} />
          <Route path="entdecken" element={<DiscoverPage />} />
          <Route path="kalender" element={<CalendarPage />} />
          <Route path="downloads" element={<DownloadsPage />} />
          <Route path="ueber" element={<AboutPage />} />
          <Route path="einstellungen" element={<SettingsPage />} />
          {/* Feste Spruenge von aussen (V4): bleiben, wie die Seiten auch heissen. */}
          <Route path="open/*" element={<OpenPage />} />
          <Route path="uebernahme" element={<Navigate to={IMPORT_TAB_PATH} replace />} />
          {/* Der Rueckweg eines Anbieters beim Verknuepfen (`/konto?linked=` oder `?error=`) landet beim Konto. */}
          <Route path="konto" element={<ToAccount />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </AuthGate>
  )
}
