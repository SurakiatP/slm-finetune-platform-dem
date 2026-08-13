import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Loader2, MailCheck, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useLanguage } from "@/i18n/LanguageContext";
import { supabase } from "@/integrations/supabase/client";
import { useToast } from "@/hooks/use-toast";
import { describeAuthError } from "@/lib/authError";

const ForgotPassword = () => {
  const { t } = useLanguage();
  const { toast } = useToast();
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setLoading(true);
    const { error } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: `${window.location.origin}/reset-password`,
    });
    setLoading(false);

    if (error) {
      toast({ title: t("passwordReset.requestFailed"), description: describeAuthError(error), variant: "destructive" });
      return;
    }
    setSubmitted(true);
  };

  if (submitted) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-secondary/20 p-4">
        <Card className="w-full max-w-md text-center">
          <CardHeader>
            <MailCheck className="mx-auto mb-3 h-12 w-12 text-primary" aria-hidden="true" />
            <CardTitle>{t("passwordReset.checkEmailTitle")}</CardTitle>
            <CardDescription>{t("passwordReset.checkEmailDescription")}</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">{t("passwordReset.checkEmailHint")}</p>
          </CardContent>
          <CardFooter className="justify-center">
            <Link to="/login" className="text-sm font-medium text-primary hover:underline">{t("passwordReset.backToLogin")}</Link>
          </CardFooter>
        </Card>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-secondary/20 p-4">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <Link to="/" className="inline-flex items-center justify-center gap-2 mb-4">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary"><Zap className="h-4 w-4 text-primary-foreground" /></div>
            <span className="font-semibold text-lg">TuneLab</span>
          </Link>
          <CardTitle className="text-xl">{t("passwordReset.requestTitle")}</CardTitle>
          <CardDescription>{t("passwordReset.requestDescription")}</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="email">{t("login.email")}</Label>
              <Input id="email" type="email" placeholder="you@company.com" value={email} onChange={(event) => setEmail(event.target.value)} required autoComplete="email" />
            </div>
            <Button type="submit" className="w-full" disabled={loading}>
              {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("passwordReset.sendLink")}
            </Button>
          </form>
        </CardContent>
        <CardFooter className="justify-center">
          <Link to="/login" className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"><ArrowLeft className="h-4 w-4" />{t("passwordReset.backToLogin")}</Link>
        </CardFooter>
      </Card>
    </div>
  );
};

export default ForgotPassword;
