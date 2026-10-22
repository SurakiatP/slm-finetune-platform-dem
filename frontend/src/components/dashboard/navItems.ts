import {
  LayoutDashboard,
  FolderKanban,
  Box,
  MessageSquare,
  Database,
  ClipboardList,
  Wallet,
  Key,
  Settings,
  BarChart3,
  Rocket,
  Trophy,
  LayoutTemplate,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  titleKey: string;
  url: string;
  icon: LucideIcon;
}

/** Main navigation, shared by the sidebar and the command palette so a page
 *  added here is reachable from both. */
export const navItems: NavItem[] = [
  { titleKey: "nav.dashboard", url: "/dashboard", icon: LayoutDashboard },
  { titleKey: "nav.projects", url: "/projects", icon: FolderKanban },
  { titleKey: "nav.models", url: "/models", icon: Box },
  { titleKey: "datasetsPage.navLabel", url: "/datasets", icon: Database },
  { titleKey: "evaluationsPage.navLabel", url: "/evaluations", icon: ClipboardList },
  { titleKey: "nav.playground", url: "/playground", icon: MessageSquare },
  { titleKey: "nav.usage", url: "/usage", icon: Wallet },
  { titleKey: "nav.templates", url: "/templates", icon: LayoutTemplate },
  { titleKey: "nav.analytics", url: "/analytics", icon: BarChart3 },
  { titleKey: "nav.deployment", url: "/deployment", icon: Rocket },
  { titleKey: "nav.leaderboard", url: "/leaderboard", icon: Trophy },
  { titleKey: "nav.apiKeys", url: "/api-keys", icon: Key },
  { titleKey: "nav.settings", url: "/settings", icon: Settings },
];
