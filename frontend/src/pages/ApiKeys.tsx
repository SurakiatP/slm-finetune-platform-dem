import { ApiKeyManager } from '@/components/settings/ApiKeyManager'
import { PageTransition } from '@/components/motion'

export default function ApiKeysPage() {
  return (
    <PageTransition>
      <div className="mx-auto max-w-4xl">
        <ApiKeyManager />
      </div>
    </PageTransition>
  )
}
