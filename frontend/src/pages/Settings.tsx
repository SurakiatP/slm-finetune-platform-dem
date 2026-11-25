import { AccountSecurity } from '@/components/settings/AccountSecurity'
import { PageTransition } from '@/components/motion'
import { useLanguage } from '@/i18n/LanguageContext'

export default function Settings() {
  const { t } = useLanguage()
  return (
    <PageTransition>
      <div className="mx-auto max-w-4xl space-y-6">
        <div>
          <h1 className="text-2xl font-bold">{t('nav.settings')}</h1>
          <p className="text-sm text-muted-foreground">{t('settings.description')}</p>
        </div>
        <AccountSecurity />
      </div>
    </PageTransition>
  )
}
