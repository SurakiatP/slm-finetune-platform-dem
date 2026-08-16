import { useState } from "react";
import { Bell, CheckCircle2, AlertTriangle, Rocket, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useLanguage } from "@/i18n/LanguageContext";

interface Notification {
  id: string;
  icon: React.ElementType;
  titleKey: string;
  description: string;
  timestamp: string;
  read: boolean;
}

const initialNotifications: Notification[] = [
  {
    id: "n1",
    icon: CheckCircle2,
    titleKey: "notif.trainingComplete",
    description: "Customer Intent Classifier finished with 94.2% accuracy.",
    timestamp: "2 hours ago",
    read: false,
  },
  {
    id: "n2",
    icon: AlertTriangle,
    titleKey: "notif.creditWarning",
    description: "You have 150 credits remaining. Consider upgrading your plan.",
    timestamp: "5 hours ago",
    read: false,
  },
  {
    id: "n3",
    icon: Rocket,
    titleKey: "notif.modelDeployed",
    description: "intent-classifier-v1 is now live on the API endpoint.",
    timestamp: "1 day ago",
    read: false,
  },
  {
    id: "n4",
    icon: Users,
    titleKey: "notif.teamInvite",
    description: "john@example.com accepted your team invitation.",
    timestamp: "2 days ago",
    read: true,
  },
];

export function NotificationCenter() {
  const [notifications, setNotifications] = useState(initialNotifications);
  const unreadCount = notifications.filter((n) => !n.read).length;
  const { t } = useLanguage();

  const markAllRead = () => {
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
  };

  const markRead = (id: string) => {
    setNotifications((prev) =>
      prev.map((n) => (n.id === id ? { ...n, read: true } : n))
    );
  };

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="h-8 w-8 relative">
          <Bell className="h-4 w-4" />
          {unreadCount > 0 && (
            <span className="absolute -top-0.5 -right-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-destructive text-[10px] font-bold text-destructive-foreground">
              {unreadCount}
            </span>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        <div className="flex items-center justify-between border-b px-4 py-3">
          <h4 className="text-sm font-semibold">{t("notif.title")}</h4>
          {unreadCount > 0 && (
            <button
              onClick={markAllRead}
              className="text-xs text-primary hover:underline"
            >
              {t("notif.markAllRead")}
            </button>
          )}
        </div>
        <div className="max-h-80 overflow-y-auto">
          {notifications.map((n) => (
            <button
              key={n.id}
              onClick={() => markRead(n.id)}
              className={`flex w-full gap-3 px-4 py-3 text-left hover:bg-accent/50 transition-colors ${
                !n.read ? "bg-accent/20" : ""
              }`}
            >
              <n.icon className="h-4 w-4 mt-0.5 shrink-0 text-muted-foreground" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium leading-tight">{t(n.titleKey)}</p>
                <p className="text-xs text-muted-foreground mt-0.5 truncate">{n.description}</p>
                <p className="text-[10px] text-muted-foreground mt-1">{n.timestamp}</p>
              </div>
              {!n.read && (
                <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-primary" />
              )}
            </button>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
