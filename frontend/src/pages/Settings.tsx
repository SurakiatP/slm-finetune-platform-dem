import { AccountSecurity } from '@/components/settings/AccountSecurity'
import { ApiKeyManager } from '@/components/settings/ApiKeyManager'
import { PageTransition } from '@/components/motion'

export default function Settings() {
  return (
    <PageTransition>
      <div className="mx-auto max-w-4xl space-y-6">
        <div>
          <h1 className="text-2xl font-bold">Settings</h1>
          <p className="text-sm text-muted-foreground">Manage your account security and API authentication keys.</p>
        </div>
        <AccountSecurity />
        <ApiKeyManager />
      </div>
    </PageTransition>
  )
}
