import {
  LayoutDashboard,
  FolderKanban,
  Box,
  MessageSquare,
  Database,
  ClipboardList,
  Wallet,
  Zap,
  Key,
  Settings,
  BarChart3,
  Rocket,
  Trophy,
  LayoutTemplate,
  LogOut,
} from "lucide-react";
import { NavLink } from "@/components/NavLink";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarFooter,
} from "@/components/ui/sidebar";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useLanguage } from "@/i18n/LanguageContext";
import { useAuth } from "@/contexts/AuthContext";
import { useNavigate } from "react-router-dom";

const navItems = [
  { titleKey: "nav.dashboard", url: "/dashboard", icon: LayoutDashboard },
  { titleKey: "nav.projects", url: "/projects", icon: FolderKanban },
  { titleKey: "nav.models", url: "/models", icon: Box },
  { titleKey: "datasetsPage.navLabel", url: "/datasets", icon: Database },
  { titleKey: "evaluationsPage.navLabel", url: "/evaluations", icon: ClipboardList },
  { titleKey: "nav.playground", url: "/playground", icon: MessageSquare },
  { titleKey: "nav.usage", url: "/usage", icon: Wallet },
  { titleKey: "nav.templates", url: "/templates", icon: LayoutTemplate, prototype: true },
  { titleKey: "nav.analytics", url: "/analytics", icon: BarChart3, prototype: true },
  { titleKey: "nav.deployment", url: "/deployment", icon: Rocket, prototype: true },
  { titleKey: "nav.leaderboard", url: "/leaderboard", icon: Trophy, prototype: true },
  { titleKey: "nav.apiKeys", url: "/api-keys", icon: Key, prototype: true },
  { titleKey: "nav.settings", url: "/settings", icon: Settings },
];

export function AppSidebar() {
  const { t } = useLanguage();
  const { user, profile, signOut } = useAuth();
  const navigate = useNavigate();
  const displayName = profile?.display_name ?? user?.email?.split("@")[0] ?? "User";
  const initials = displayName.slice(0, 2).toUpperCase();

  const handleSignOut = async () => {
    await signOut();
    navigate("/login", { replace: true });
  };

  return (
    <Sidebar collapsible="icon">
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel className="px-3 py-2">
            <div className="flex items-center gap-2">
              <Zap className="h-5 w-5 text-primary" />
              <span className="font-bold text-sm text-foreground">SLM Studio</span>
            </div>
          </SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {navItems.map((item) => (
                <SidebarMenuItem key={item.titleKey}>
                  <SidebarMenuButton asChild tooltip={t(item.titleKey)}>
                    <NavLink
                      to={item.url}
                      className="flex items-center gap-3 px-3 py-2 rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors"
                      activeClassName="bg-accent text-accent-foreground font-medium"
                    >
                      <item.icon className="h-4 w-4 shrink-0" />
                      <span>{t(item.titleKey)}</span>
                      {item.prototype ? <Badge variant="outline" className="ml-auto text-[9px]">Prototype</Badge> : null}
                    </NavLink>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter className="border-t border-border p-3">
        {user ? (
          <div className="flex items-center gap-2">
            <Avatar className="h-7 w-7 shrink-0">
              <AvatarFallback className="bg-primary/10 text-[10px] font-semibold text-primary">{initials}</AvatarFallback>
            </Avatar>
            <div className="min-w-0 flex-1 group-data-[collapsible=icon]:hidden">
              <p className="truncate text-xs font-medium">{displayName}</p>
              <p className="truncate text-[10px] text-muted-foreground">{user.email}</p>
            </div>
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => void handleSignOut()} title={t("auth.signOut")}>
              <LogOut className="h-3.5 w-3.5" />
            </Button>
          </div>
        ) : null}
      </SidebarFooter>
    </Sidebar>
  );
}
