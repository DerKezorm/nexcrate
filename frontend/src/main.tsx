import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import { URL_BASE } from './lib/base'

import App from './App'
import { AuthProvider } from './auth/AuthProvider'
import { startI18n } from './i18n'
import './styles/index.css'

function startApp(): void {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <BrowserRouter basename={URL_BASE || undefined}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </StrictMode>,
  )
}

function startFailed(): void {
  const root = document.getElementById('root')
  if (root) {
    root.innerHTML =
      '<p style="font-family:system-ui;padding:2rem;color:#c3c3ce">nexcrate could not load its texts. Reload the page.</p>'
  }
}

// Erst die Texte, dann die Oberflaeche. Sonst stuende einen Wimpernschlag lang
// die rohe Schluesselliste auf dem Bildschirm.
startI18n().then(startApp, startFailed)
