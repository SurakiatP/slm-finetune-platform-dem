import { useLocation, useOutlet } from "react-router-dom";
import { SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { AppSidebar } from "./AppSidebar";
import { AnimatePresence, motion } from "framer-motion";
import { ThemeToggle } from "@/components/ThemeToggle";
import { NotificationCenter } from "@/components/NotificationCenter";
import { CommandPalette } from "@/components/CommandPalette";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { OnboardingTour } from "@/components/OnboardingTour";
import { useRouteTitle } from "@/hooks/useRouteTitle";
import { BackendStatus } from "@/components/engine/BackendStatus";
import { Suspense } from "react";
import { Loader2 } from "lucide-react";

function PageLoading() {
  return (
    <div className="flex min-h-[40vh] items-center justify-center" role="status" aria-live="polite">
      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-hidden="true" />
      <span className="sr-only">Loading…</span>
    </div>
  );
}

function AnimatedOutlet() {
  const location = useLocation();
  const outlet = useOutlet();

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={location.pathname}
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.18, ease: [0.25, 0.1, 0.25, 1] }}
      >
        {/* Dashboard pages are lazy-loaded; keep the shell visible while a page chunk loads. */}
        <Suspense fallback={<PageLoading />}>{outlet}</Suspense>
      </motion.div>
    </AnimatePresence>
  );
}

export default function DashboardLayout() {
  useRouteTitle();
  return (
    <SidebarProvider>
      <OnboardingTour />
      <div className="min-h-screen flex w-full">
        <AppSidebar />
        <div className="flex-1 flex flex-col min-w-0">
          <header className="sticky top-0 z-10 flex h-14 items-center bg-background/90 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/75">
            <SidebarTrigger className="mr-4" />
            <span className="text-sm font-medium text-muted-foreground">SLM Fine-Tuning Platform</span>
            <div className="ml-auto flex items-center gap-1">
              <BackendStatus />
              <CommandPalette />
              <NotificationCenter />
              <LanguageSwitcher />
              <ThemeToggle />
            </div>
          </header>
          <main className="flex-1 overflow-auto px-6 pb-10 pt-2">
            <div className="mx-auto w-full max-w-7xl">
            <AnimatedOutlet />
            </div>
          </main>
        </div>
      </div>
    </SidebarProvider>
  );
}
