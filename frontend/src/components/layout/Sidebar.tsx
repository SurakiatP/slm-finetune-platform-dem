import { Boxes, Cpu, FolderKanban, LayoutDashboard, LogOut, MessagesSquare } from 'lucide-react'
import { NavLink } from 'react-router-dom'

import { useAuth } from '@/auth/AuthProvider'
import { cn } from '@/lib/cn'

const navItems = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/projects', label: 'Projects', icon: FolderKanban, end: false },
  { to: '/models', label: 'Models', icon: Boxes, end: false },
  { to: '/playground', label: 'Playground', icon: MessagesSquare, end: false },
]

export function Sidebar() {
  const { enabled, session, signOut } = useAuth()
  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-line/60 bg-surface max-lg:w-14">
      <div className="flex h-14 items-center gap-2 border-b border-line/60 px-4 max-lg:justify-center max-lg:px-0">
        <Cpu className="h-5 w-5 shrink-0 text-accent" aria-hidden />
        <span className="truncate font-mono text-sm font-semibold max-lg:hidden">SLM Platform</span>
      </div>
      <nav aria-label="Primary" className="flex flex-1 flex-col gap-1 p-2">
        {navItems.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors max-lg:justify-center max-lg:px-0',
                isActive
                  ? 'bg-accent-muted font-medium text-accent'
                  : 'text-body-muted hover:bg-surface-2 hover:text-body',
              )
            }
          >
            <Icon className="h-4 w-4 shrink-0" aria-hidden />
            <span className="max-lg:sr-only">{label}</span>
          </NavLink>
        ))}
      </nav>
      {enabled && session && (
        <div className="border-t border-line/60 p-2">
          <button
            type="button"
            onClick={() => void signOut()}
            title={`Sign out (${session.user.email ?? 'signed in'})`}
            className="flex w-full cursor-pointer items-center gap-2.5 rounded-md px-3 py-2 text-sm text-body-muted transition-colors hover:bg-surface-2 hover:text-body max-lg:justify-center max-lg:px-0"
          >
            <LogOut className="h-4 w-4 shrink-0" aria-hidden />
            <span className="truncate max-lg:sr-only">{session.user.email ?? 'Sign out'}</span>
          </button>
        </div>
      )}
      <div className="border-t border-line/60 p-3 max-lg:hidden">
        <p className="font-mono text-[10px] text-body-muted/60">SLM Fine-Tuning PoC</p>
      </div>
    </aside>
  )
}
