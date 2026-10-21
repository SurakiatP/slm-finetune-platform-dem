import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { useLanguage } from "@/i18n/LanguageContext";

const APP_NAME = "TuneLab";

/** Longest-prefix match: first entry whose prefix the pathname starts with wins,
 *  so more specific routes (`/projects/new`) must precede `/projects`. */
const ROUTE_TITLE_KEYS: Array<[prefix: string, key: string]> = [
  ["/dashboard", "nav.dashboard"],
  ["/projects/new", "newProject.title"],
  ["/projects", "nav.projects"],
  ["/models", "nav.models"],
  ["/datasets", "datasetsPage.title"],
  ["/evaluations", "evaluationsPage.title"],
  ["/usage", "nav.usage"],
  ["/playground", "nav.playground"],
  ["/analytics", "nav.analytics"],
  ["/deployment", "nav.deployment"],
  ["/leaderboard", "nav.leaderboard"],
  ["/templates", "nav.templates"],
  ["/settings", "nav.settings"],
  ["/api-keys", "nav.apiKeys"],
];

/** Keeps `document.title` in step with the current route and language, so
 *  browser tabs, history and bookmarks name the page instead of the app shell. */
export function useRouteTitle(): void {
  const { pathname } = useLocation();
  const { t } = useLanguage();

  useEffect(() => {
    const match = ROUTE_TITLE_KEYS.find(([prefix]) => pathname === prefix || pathname.startsWith(`${prefix}/`));
    document.title = match ? `${t(match[1])} · ${APP_NAME}` : APP_NAME;
  }, [pathname, t]);
}
