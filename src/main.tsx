import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from '@tanstack/react-router'
import './index.css'
import { router } from './router'
import { OptiFrameProvider } from './store/OptiFrameStore'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <OptiFrameProvider>
      <RouterProvider router={router} />
    </OptiFrameProvider>
  </StrictMode>,
)
