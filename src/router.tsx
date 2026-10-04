import { createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { RootLayout } from './routes/RootLayout'
import { CapturePage } from './routes/CapturePage'
import { MesurerPage } from './routes/MesurerPage'
import { MonturePage } from './routes/MonturePage'
import { PasAPasPage } from './routes/PasAPasPage'
import { AProposPage } from './routes/AProposPage'

const rootRoute = createRootRoute({ component: RootLayout })

const captureRoute = createRoute({ getParentRoute: () => rootRoute, path: '/', component: CapturePage })
const mesurerRoute = createRoute({ getParentRoute: () => rootRoute, path: '/mesurer', component: MesurerPage })
const montureRoute = createRoute({ getParentRoute: () => rootRoute, path: '/monture', component: MonturePage })
const pasAPasRoute = createRoute({ getParentRoute: () => rootRoute, path: '/pas-a-pas', component: PasAPasPage })
const aProposRoute = createRoute({ getParentRoute: () => rootRoute, path: '/a-propos', component: AProposPage })

const routeTree = rootRoute.addChildren([captureRoute, mesurerRoute, montureRoute, pasAPasRoute, aProposRoute])

export const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
