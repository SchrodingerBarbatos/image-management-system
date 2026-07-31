import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './theme/global.css'

try {
  document.documentElement.dataset.theme =
    localStorage.getItem('image-library-theme') === 'dark' ? 'dark' : 'light'
} catch {
  document.documentElement.dataset.theme = 'light'
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
