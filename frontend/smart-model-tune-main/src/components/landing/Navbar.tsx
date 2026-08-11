import { useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Zap, Menu } from "lucide-react";
import { ThemeToggle } from "@/components/ThemeToggle";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet";
import { useLanguage } from "@/i18n/LanguageContext";

const Navbar = () => {
  const [open, setOpen] = useState(false);
  const { t } = useLanguage();

  return (
    <header className="fixed top-0 left-0 right-0 z-50 glass">
      <div className="container flex h-16 items-center justify-between">
        <Link to="/" className="flex items-center gap-2 font-semibold text-lg">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary">
            <Zap className="h-4 w-4 text-primary-foreground" />
          </div>
          <span>TuneLab</span>
        </Link>

        <nav className="hidden md:flex items-center gap-8 text-sm text-muted-foreground">
          <a href="#features" className="hover:text-foreground transition-colors">{t("landing.features")}</a>
          <a href="#use-cases" className="hover:text-foreground transition-colors">{t("landing.useCases")}</a>
          <a href="#pricing" className="hover:text-foreground transition-colors">{t("landing.pricing")}</a>
        </nav>

        <div className="hidden md:flex items-center gap-3">
          <LanguageSwitcher />
          <ThemeToggle />
          <Button variant="ghost" size="sm" asChild>
            <Link to="/login">{t("landing.login")}</Link>
          </Button>
          <Button size="sm" asChild>
            <Link to="/signup">{t("landing.getStarted")}</Link>
          </Button>
        </div>

        {/* Mobile menu */}
        <div className="flex md:hidden items-center gap-2">
          <LanguageSwitcher />
          <ThemeToggle />
          <Sheet open={open} onOpenChange={setOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon">
                <Menu className="h-5 w-5" />
              </Button>
            </SheetTrigger>
            <SheetContent side="right" className="w-72">
              <div className="flex flex-col gap-6 mt-8">
                <nav className="flex flex-col gap-4 text-sm">
                  <a href="#features" onClick={() => setOpen(false)} className="hover:text-foreground transition-colors text-muted-foreground">{t("landing.features")}</a>
                  <a href="#use-cases" onClick={() => setOpen(false)} className="hover:text-foreground transition-colors text-muted-foreground">{t("landing.useCases")}</a>
                  <a href="#pricing" onClick={() => setOpen(false)} className="hover:text-foreground transition-colors text-muted-foreground">{t("landing.pricing")}</a>
                </nav>
                <div className="flex flex-col gap-2">
                  <Button variant="ghost" asChild onClick={() => setOpen(false)}>
                    <Link to="/login">{t("landing.login")}</Link>
                  </Button>
                  <Button asChild onClick={() => setOpen(false)}>
                    <Link to="/signup">{t("landing.getStarted")}</Link>
                  </Button>
                </div>
              </div>
            </SheetContent>
          </Sheet>
        </div>
      </div>
    </header>
  );
};

export default Navbar;
