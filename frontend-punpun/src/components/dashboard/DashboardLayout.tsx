import { useLocation, useOutlet } from "react-router-dom";
import { SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { AppSidebar } from "./AppSidebar";
import { AnimatePresence, motion } from "framer-motion";
import { ThemeToggle } from "@/components/ThemeToggle";
import { NotificationCenter } from "@/components/NotificationCenter";
import { CommandPalette } from "@/components/CommandPalette";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { OnboardingTour } from "@/components/OnboardingTour";

function AnimatedOutlet() {
  const location = useLocation();
  const outlet = useOutlet();

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={location.pathname}
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -8 }}
        transition={{ duration: 0.25, ease: [0.25, 0.1, 0.25, 1] }}
      >
        {outlet}
      </motion.div>
    </AnimatePresence>
  );
}

export default function DashboardLayout() {
  return (
    <SidebarProvider>
      <OnboardingTour />
      <div className="min-h-screen flex w-full">
        <AppSidebar />
        <div className="flex-1 flex flex-col min-w-0">
          <header className="h-14 flex items-center border-b border-border px-4 bg-background sticky top-0 z-10">
            <SidebarTrigger className="mr-4" />
            <span className="text-sm font-medium text-muted-foreground">SLM Fine-Tuning Platform</span>
            <div className="ml-auto flex items-center gap-1">
              <CommandPalette />
              <NotificationCenter />
              <LanguageSwitcher />
              <ThemeToggle />
            </div>
          </header>
          <main className="flex-1 p-6 overflow-auto">
            <AnimatedOutlet />
          </main>
        </div>
      </div>
    </SidebarProvider>
  );
}
