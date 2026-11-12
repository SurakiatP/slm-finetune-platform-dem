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

export type NavGroup = "build" | "ship" | "account";

export interface NavItem {
  titleKey: string;
  group: NavGroup;
  url: string;
  icon: LucideIcon;
}

/** Main navigation, shared by the sidebar and the command palette so a page
 *  added here is reachable from both. */
export const navItems: NavItem[] = [
  { titleKey: "nav.dashboard", group: "build", url: "/dashboard", icon: LayoutDashboard },
  { titleKey: "nav.projects", group: "build", url: "/projects", icon: FolderKanban },
  { titleKey: "nav.models", group: "build", url: "/models", icon: Box },
  { titleKey: "datasetsPage.navLabel", group: "build", url: "/datasets", icon: Database },
  { titleKey: "evaluationsPage.navLabel", group: "build", url: "/evaluations", icon: ClipboardList },
  { titleKey: "nav.playground", group: "build", url: "/playground", icon: MessageSquare },
  { titleKey: "nav.templates", group: "ship", url: "/templates", icon: LayoutTemplate },
  { titleKey: "nav.analytics", group: "ship", url: "/analytics", icon: BarChart3 },
  { titleKey: "nav.deployment", group: "ship", url: "/deployment", icon: Rocket },
  { titleKey: "nav.leaderboard", group: "ship", url: "/leaderboard", icon: Trophy },
  { titleKey: "nav.usage", group: "account", url: "/usage", icon: Wallet },
  { titleKey: "nav.apiKeys", group: "account", url: "/api-keys", icon: Key },
  { titleKey: "nav.settings", group: "account", url: "/settings", icon: Settings },
];

/** Section headings shown above each group in the sidebar; the first group has none. */
export const navGroups: { id: NavGroup; labelKey?: string }[] = [
  { id: "build" },
  { id: "ship", labelKey: "nav.groupShip" },
  { id: "account", labelKey: "nav.groupAccount" },
];
