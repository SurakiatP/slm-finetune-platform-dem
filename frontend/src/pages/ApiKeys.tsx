import { ApiKeyManager } from '@/components/settings/ApiKeyManager'
import { PageTransition } from '@/components/motion'
import { useLanguage } from '@/i18n/LanguageContext'

export default function ApiKeysPage() {
  const { t } = useLanguage()
  return (
    <PageTransition>
      <div className="mx-auto max-w-4xl space-y-6">
        <div>
          <h1 className="text-2xl font-bold">{t('nav.apiKeys')}</h1>
          <p className="text-sm text-muted-foreground">{t('apiKeysPage.subtitle')}</p>
        </div>
        <ApiKeyManager />
      </div>
    </PageTransition>
  )
}
