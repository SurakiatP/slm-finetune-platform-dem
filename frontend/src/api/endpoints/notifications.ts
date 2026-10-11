import { api, pageQuery } from '@/api/client'

export type NotificationType =
  | 'dataset.completed' | 'dataset.failed'
  | 'training.completed' | 'training.failed'
  | 'evaluation.completed' | 'evaluation.failed'
  | 'export.completed' | 'export.failed'
  | 'deployment.ready' | 'deployment.failed'

export interface UserNotification {
  id: string
  event_type: NotificationType
  resource_type: 'dataset' | 'training' | 'evaluation' | 'model' | 'deployment'
  resource_id: string
  payload: Record<string, string | number | boolean | null>
  created_at: string
  read_at: string | null
}

export interface NotificationPage {
  items: UserNotification[]
  next_cursor: string | null
  unread_count: number
}

const BASE = '/api/v1/notifications'

export function listNotifications(cursor?: string): Promise<NotificationPage> {
  return api.get(`${BASE}${pageQuery({ cursor, limit: 20 })}`)
}

export function markNotificationRead(id: string): Promise<UserNotification> {
  return api.patch(`${BASE}/${encodeURIComponent(id)}/read`, {})
}

export function markAllNotificationsRead(): Promise<{ marked_count: number }> {
  return api.post(`${BASE}/read-all`)
}
