import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useLanguage } from "@/i18n/LanguageContext";

export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, loading, mfaLoading, mfaError, mfaRequired, refreshMfa } = useAuth();
  const { t } = useLanguage();
  const location = useLocation();

  if (loading || mfaLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  if (mfaError) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4 p-4 text-center">
        <p className="text-sm text-destructive" role="alert">{t("security.mfaStatusError")}</p>
        <Button variant="outline" onClick={() => void refreshMfa()}>{t("security.retry")}</Button>
      </div>
    );
  }

  if (mfaRequired) {
    return <Navigate to="/mfa" state={{ from: location }} replace />;
  }

  return <>{children}</>;
}
