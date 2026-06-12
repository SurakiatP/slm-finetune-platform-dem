import { SearchX } from 'lucide-react'
import { Link } from 'react-router-dom'

import { EmptyState } from '@/components/ui/EmptyState'

export function NotFoundPage() {
  return (
    <EmptyState
      icon={SearchX}
      title="Page not found"
      description="The page you are looking for does not exist."
      action={
        <Link to="/" className="text-sm font-medium text-accent hover:underline">
          Back to dashboard
        </Link>
      }
    />
  )
}
