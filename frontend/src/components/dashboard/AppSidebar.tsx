import { Zap, LogOut } from "lucide-react";
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
import { Button } from "@/components/ui/button";
import { useLanguage } from "@/i18n/LanguageContext";
import { useAuth } from "@/contexts/AuthContext";
import { useNavigate } from "react-router-dom";
import { navGroups, navItems } from "./navItems";


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
      <SidebarContent className="gap-0 px-2 pt-3">
        <div className="flex items-center gap-2.5 px-3 pb-3 pt-1">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Zap className="h-3.5 w-3.5" aria-hidden="true" />
          </span>
          <span className="text-[15px] font-semibold tracking-tight text-foreground group-data-[collapsible=icon]:hidden">SLM Studio</span>
        </div>
        {navGroups.map((group) => (
          <SidebarGroup key={group.id} className="py-1">
            {group.labelKey ? (
              <SidebarGroupLabel className="h-7 px-3 text-xs font-normal text-muted-foreground">{t(group.labelKey)}</SidebarGroupLabel>
            ) : null}
            <SidebarGroupContent>
              <SidebarMenu className="gap-0.5">
                {navItems
                  .filter((item) => item.group === group.id)
                  .map((item) => (
                    <SidebarMenuItem key={item.titleKey}>
                      <SidebarMenuButton asChild tooltip={t(item.titleKey)}>
                        <NavLink
                          to={item.url}
                          className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-card/70 hover:text-foreground dark:hover:bg-muted"
                          activeClassName="bg-card text-foreground font-medium shadow-card dark:bg-accent"
                        >
                          <item.icon className="h-4 w-4 shrink-0" />
                          <span>{t(item.titleKey)}</span>
                        </NavLink>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>
      <SidebarFooter className="p-3">
        {user ? (
          <div className="flex items-center gap-2">
            <Avatar className="h-7 w-7 shrink-0">
              <AvatarFallback className="bg-primary/10 text-xs font-semibold text-primary">{initials}</AvatarFallback>
            </Avatar>
            <div className="min-w-0 flex-1 group-data-[collapsible=icon]:hidden">
              <p className="truncate text-sm font-medium">{displayName}</p>
              <p className="truncate text-xs text-muted-foreground">{user.email}</p>
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
