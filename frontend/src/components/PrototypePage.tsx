import { FlaskConical } from 'lucide-react'

import { PageTransition } from '@/components/motion'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

interface PrototypePageProps {
  title: string
  description: string
}

export function PrototypePage({ title, description }: PrototypePageProps) {
  return (
    <PageTransition>
      <div className="mx-auto max-w-3xl space-y-6">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold">{title}</h1>
          <Badge variant="outline">Prototype</Badge>
        </div>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <FlaskConical className="h-4 w-4 text-primary" />
              UI preview only
            </CardTitle>
            <CardDescription>{description}</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">
              The Engine does not expose a production API for this feature yet. Controls that
              previously wrote business data to Supabase have been disabled until a backend
              contract is available.
            </p>
          </CardContent>
        </Card>
      </div>
    </PageTransition>
  )
}

