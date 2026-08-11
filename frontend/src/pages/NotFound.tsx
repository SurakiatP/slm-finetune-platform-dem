import { useLocation, Link, Navigate } from "react-router-dom";
import { useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Home, ArrowLeft } from "lucide-react";
import { useLanguage } from "@/i18n/LanguageContext";
import { useAuth } from "@/contexts/AuthContext";

// Paths that were removed — redirect to a sensible replacement instead of 404
const REMOVED_ROUTE_REDIRECTS: Record<string, string> = {
  "/calculator": "/dashboard",
  "/datasets": "/dashboard",
  "/dataset-explorer": "/dashboard",
  "/annotation": "/dashboard",
  "/annotation-tool": "/dashboard",
  "/billing": "/settings",
  "/team": "/settings",
  "/settings/billing": "/settings",
  "/settings/team": "/settings",
};

const NotFound = () => {
  const location = useLocation();
  const { t } = useLanguage();
  const { user } = useAuth();

  const redirectTarget = REMOVED_ROUTE_REDIRECTS[location.pathname];

  useEffect(() => {
    if (!redirectTarget) {
      console.warn("404: route not found", location.pathname);
    }
  }, [location.pathname, redirectTarget]);

  if (redirectTarget) {
    return <Navigate to={user ? redirectTarget : "/"} replace />;
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="text-center max-w-md">
        <h1 className="mb-2 text-7xl font-bold text-primary">404</h1>
        <p className="mb-2 text-xl font-semibold">{t("notFound.title")}</p>
        <p className="mb-6 text-sm text-muted-foreground">
          {t("notFound.description")}{" "}
          <code className="px-1.5 py-0.5 rounded bg-muted text-xs">{location.pathname}</code>
        </p>
        <div className="flex gap-2 justify-center">
          <Button variant="outline" onClick={() => window.history.back()} className="gap-1.5">
            <ArrowLeft className="h-4 w-4" /> {t("notFound.back")}
          </Button>
          <Button asChild className="gap-1.5">
            <Link to={user ? "/dashboard" : "/"}>
              <Home className="h-4 w-4" /> {t("notFound.home")}
            </Link>
          </Button>
        </div>
      </div>
    </div>
  );
};

export default NotFound;
