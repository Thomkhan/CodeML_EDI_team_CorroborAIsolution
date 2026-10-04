import { Link, Outlet } from '@tanstack/react-router'

const NAV_ITEMS = [
  { to: '/', label: 'Capture', icon: '📷' },
  { to: '/mesurer', label: 'Mesurer', icon: '📐' },
  { to: '/monture', label: 'Monture', icon: '🕶️' },
  { to: '/pas-a-pas', label: 'Pas à pas', icon: '🧭' },
  { to: '/a-propos', label: 'À propos', icon: 'ℹ️' },
] as const

export function RootLayout() {
  return (
    <>
      <header className="top-bar">
        <span className="brand">OptiFrame</span>
        <span className="pill">SN-SF · CodeML</span>
      </header>

      <main className="app-main">
        <Outlet />
      </main>

      <nav className="bottom-nav">
        {NAV_ITEMS.map((item) => (
          <Link key={item.to} to={item.to}>
            {({ isActive }: { isActive: boolean }) => (
              <span className={isActive ? 'active' : ''} style={{ display: 'contents' }}>
                <span className="icon">{item.icon}</span>
                <span>{item.label}</span>
              </span>
            )}
          </Link>
        ))}
      </nav>
    </>
  )
}
