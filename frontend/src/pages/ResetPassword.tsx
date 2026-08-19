import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, CheckCircle2, Loader2, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useLanguage } from "@/i18n/LanguageContext";
import { supabase } from "@/integrations/supabase/client";
import { useToast } from "@/hooks/use-toast";
import { describeAuthError } from "@/lib/authError";

const ResetPassword = () => {
  const { t } = useLanguage();
  const { toast } = useToast();
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [checkingSession, setCheckingSession] = useState(true);
  const [hasSession, setHasSession] = useState(false);
  const [loading, setLoading] = useState(false);
  const [completed, setCompleted] = useState(false);

  useEffect(() => {
    void supabase.auth.getSession()
      .then(({ data }) => setHasSession(Boolean(data.session)))
      .finally(() => setCheckingSession(false));
  }, []);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (password !== confirmPassword) {
      toast({ title: t("passwordReset.updateFailed"), description: t("security.passwordMismatch"), variant: "destructive" });
      return;
    }

    setLoading(true);
    const { error } = await supabase.auth.updateUser({ password });
    setLoading(false);
    if (error) {
      toast({ title: t("passwordReset.updateFailed"), description: describeAuthError(error), variant: "destructive" });
      return;
    }

    await supabase.auth.signOut({ scope: "local" });
    setCompleted(true);
  };

  const cardContent = checkingSession ? (
    <div className="flex justify-center py-8"><Loader2 className="h-6 w-6 animate-spin text-primary" /></div>
  ) : !hasSession ? (
    <>
      <CardHeader className="text-center">
        <CardTitle>{t("passwordReset.invalidLinkTitle")}</CardTitle>
        <CardDescription>{t("passwordReset.invalidLinkDescription")}</CardDescription>
      </CardHeader>
      <CardFooter className="justify-center">
        <Link to="/forgot-password" className="text-sm font-medium text-primary hover:underline">{t("passwordReset.requestNewLink")}</Link>
      </CardFooter>
    </>
  ) : completed ? (
    <>
      <CardHeader className="text-center">
        <CheckCircle2 className="mx-auto mb-3 h-12 w-12 text-primary" aria-hidden="true" />
        <CardTitle>{t("passwordReset.updatedTitle")}</CardTitle>
        <CardDescription>{t("passwordReset.updatedDescription")}</CardDescription>
      </CardHeader>
      <CardFooter className="justify-center">
        <Link to="/login" className="text-sm font-medium text-primary hover:underline">{t("passwordReset.backToLogin")}</Link>
      </CardFooter>
    </>
  ) : (
    <>
      <CardHeader className="text-center">
        <CardTitle className="text-xl">{t("passwordReset.newPasswordTitle")}</CardTitle>
        <CardDescription>{t("passwordReset.newPasswordDescription")}</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="password">{t("security.newPassword")}</Label>
            <Input id="password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} required minLength={6} autoComplete="new-password" />
            <p className="text-xs text-muted-foreground">{t("security.passwordRequirement")}</p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="confirm-password">{t("security.confirmPassword")}</Label>
            <Input id="confirm-password" type="password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} required minLength={6} autoComplete="new-password" />
          </div>
          <Button type="submit" className="w-full" disabled={loading}>
            {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {t("passwordReset.updatePassword")}
          </Button>
        </form>
      </CardContent>
    </>
  );

  return (
    <div className="min-h-screen flex items-center justify-center bg-secondary/20 p-4">
      <Card className="w-full max-w-md">
        <div className="pt-6 text-center">
          <Link to="/" className="inline-flex items-center justify-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary"><Zap className="h-4 w-4 text-primary-foreground" /></div>
            <span className="font-semibold text-lg">TuneLab</span>
          </Link>
        </div>
        {cardContent}
        {!checkingSession && hasSession && !completed && (
          <CardFooter className="justify-center">
            <Link to="/login" className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"><ArrowLeft className="h-4 w-4" />{t("passwordReset.backToLogin")}</Link>
          </CardFooter>
        )}
      </Card>
    </div>
  );
};

export default ResetPassword;
