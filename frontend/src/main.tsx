import './i18n'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import App from './App'

try {
  const tx = localStorage.getItem('abw-theme')
  if (tx) document.documentElement.dataset.theme = tx
} catch { /* ohne Speicher gilt das dunkle Standarddesign */ }

// Service Worker für Push-Meldungen und die Installation als App (Chrome: «App installieren»)
if ('serviceWorker' in navigator && window.isSecureContext) navigator.serviceWorker.register('/sw.js').catch(() => {})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
