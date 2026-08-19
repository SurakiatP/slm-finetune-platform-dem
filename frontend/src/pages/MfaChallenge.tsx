import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { Loader2, ShieldCheck } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { InputOTP, InputOTPGroup, InputOTPSlot } from "@/components/ui/input-otp";
import { useAuth } from "@/contexts/AuthContext";
import { useLanguage } from "@/i18n/LanguageContext";
import { describeAuthError } from "@/lib/authError";
import { verifyMfa } from "@/lib/accountSecurity";

interface MfaLocationState {
  from?: { pathname?: string };
}

export default function MfaChallenge() {
  const { t } = useLanguage();
  const { user, loading, mfaLoading, mfaError, mfaRequired, mfaFactors, refreshMfa } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [selectedFactorId, setSelectedFactorId] = useState("");
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const state = location.state as MfaLocationState | null;
  const destination = state?.from?.pathname || "/dashboard";
  const factorId = selectedFactorId || mfaFactors[0]?.id || "";

  if (loading || mfaLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!user) return <Navigate to="/login" replace />;
  if (mfaError) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4 p-4 text-center">
        <p className="text-sm text-destructive" role="alert">{t("security.mfaStatusError")}</p>
        <Button variant="outline" onClick={() => void refreshMfa()}>{t("security.retry")}</Button>
      </div>
    );
  }
  if (!mfaRequired) return <Navigate to={destination} replace />;

  const handleVerify = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!factorId || code.length !== 6 || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      await verifyMfa(factorId, code);
      await refreshMfa();
      navigate(destination, { replace: true });
    } catch (failure) {
      setError(describeAuthError(failure));
      setCode("");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="min-h-screen flex items-center justify-center bg-secondary/20 p-4">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <div className="mx-auto mb-2 flex h-10 w-10 items-center justify-center rounded-full bg-primary/10">
            <ShieldCheck className="h-5 w-5 text-primary" />
          </div>
          <CardTitle>{t("security.mfaChallengeTitle")}</CardTitle>
          <CardDescription>{t("security.mfaChallengeDescription")}</CardDescription>
        </CardHeader>
        <CardContent>
          {mfaFactors.length === 0 ? (
            <div className="space-y-4 text-center">
              <p className="text-sm text-destructive" role="alert">{t("security.mfaNoFactors")}</p>
              <Button variant="outline" onClick={() => void refreshMfa()}>{t("security.retry")}</Button>
            </div>
          ) : (
            <form onSubmit={handleVerify} className="space-y-5">
              {mfaFactors.length > 1 ? (
                <div className="space-y-2">
                  <Label htmlFor="mfa-factor">{t("security.mfaChooseFactor")}</Label>
                  <Select value={factorId} onValueChange={setSelectedFactorId}>
                    <SelectTrigger id="mfa-factor"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {mfaFactors.map((factor, index) => (
                        <SelectItem key={factor.id} value={factor.id}>
                          {factor.friendlyName || `${t("security.authenticatorApp")} ${index + 1}`}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              ) : null}

              <div className="space-y-2">
                <Label htmlFor="mfa-code">{t("security.verificationCode")}</Label>
                <InputOTP
                  id="mfa-code"
                  maxLength={6}
                  value={code}
                  onChange={setCode}
                  disabled={submitting}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  containerClassName="justify-center"
                >
                  <InputOTPGroup>
                    {Array.from({ length: 6 }, (_, index) => <InputOTPSlot key={index} index={index} />)}
                  </InputOTPGroup>
                </InputOTP>
              </div>

              {error ? <p className="text-sm text-destructive" role="alert">{error}</p> : null}

              <Button type="submit" className="w-full" disabled={!factorId || code.length !== 6 || submitting}>
                {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                {t("security.verifyAndContinue")}
              </Button>
            </form>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
