import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Copy, Loader2, Shield } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Input } from "@/components/ui/input";
import { InputOTP, InputOTPGroup, InputOTPSlot } from "@/components/ui/input-otp";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { useAuth } from "@/contexts/AuthContext";
import { useLanguage } from "@/i18n/LanguageContext";
import { useToast } from "@/hooks/use-toast";
import { describeAuthError } from "@/lib/authError";
import {
  changePassword,
  clearLocalAuthSession,
  deleteAccount,
  enrollMfa,
  requestPasswordReauthentication,
  unenrollMfa,
  verifyMfa,
  type MfaEnrollment,
  type MfaFactor,
} from "@/lib/accountSecurity";
import { clearAppliedTuningRuns } from "@/lib/tuningGenerator";

function errorCode(error: unknown): string | undefined {
  return (error as { code?: string })?.code;
}

function ChangePasswordControl() {
  const { t } = useLanguage();
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [nonce, setNonce] = useState("");
  const [awaitingNonce, setAwaitingNonce] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const reset = () => {
    setNewPassword("");
    setConfirmPassword("");
    setNonce("");
    setAwaitingNonce(false);
    setSubmitting(false);
    setError("");
  };

  const handleOpenChange = (nextOpen: boolean) => {
    setOpen(nextOpen);
    if (!nextOpen) reset();
  };

  const validatePassword = () => {
    if (newPassword.length < 6) return t("security.passwordTooShort");
    if (newPassword !== confirmPassword) return t("security.passwordMismatch");
    return "";
  };

  const finishSuccess = () => {
    toast({ title: t("security.passwordUpdated") });
    setOpen(false);
    reset();
  };

  const submitPassword = async (event: React.FormEvent) => {
    event.preventDefault();
    const validationError = validatePassword();
    if (validationError || submitting) {
      setError(validationError);
      return;
    }

    setSubmitting(true);
    setError("");
    try {
      await changePassword(newPassword);
      finishSuccess();
    } catch (failure) {
      if (errorCode(failure) === "reauthentication_needed") {
        try {
          await requestPasswordReauthentication();
          setAwaitingNonce(true);
          toast({ title: t("security.reauthCodeSent") });
        } catch (reauthFailure) {
          setError(describeAuthError(reauthFailure));
        }
      } else {
        setError(describeAuthError(failure));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const submitNonce = async (event: React.FormEvent) => {
    event.preventDefault();
    if (nonce.length !== 6 || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      await changePassword(newPassword, nonce);
      finishSuccess();
    } catch (failure) {
      setError(describeAuthError(failure));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>{t("security.changePassword")}</Button>
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("security.changePassword")}</DialogTitle>
            <DialogDescription>
              {awaitingNonce ? t("security.reauthDescription") : t("security.passwordDialogDescription")}
            </DialogDescription>
          </DialogHeader>

          {awaitingNonce ? (
            <form onSubmit={submitNonce} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="password-nonce">{t("security.verificationCode")}</Label>
                <InputOTP
                  id="password-nonce"
                  maxLength={6}
                  value={nonce}
                  onChange={setNonce}
                  disabled={submitting}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                >
                  <InputOTPGroup>
                    {Array.from({ length: 6 }, (_, index) => <InputOTPSlot key={index} index={index} />)}
                  </InputOTPGroup>
                </InputOTP>
              </div>
              {error ? <p className="text-sm text-destructive" role="alert">{error}</p> : null}
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => handleOpenChange(false)} disabled={submitting}>{t("security.cancel")}</Button>
                <Button type="submit" disabled={nonce.length !== 6 || submitting}>
                  {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                  {t("security.updatePassword")}
                </Button>
              </DialogFooter>
            </form>
          ) : (
            <form onSubmit={submitPassword} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="new-password">{t("security.newPassword")}</Label>
                <Input id="new-password" type="password" minLength={6} autoComplete="new-password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} disabled={submitting} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="confirm-password">{t("security.confirmPassword")}</Label>
                <Input id="confirm-password" type="password" minLength={6} autoComplete="new-password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} disabled={submitting} />
              </div>
              <p className="text-xs text-muted-foreground">{t("security.passwordRequirement")}</p>
              {error ? <p className="text-sm text-destructive" role="alert">{error}</p> : null}
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => handleOpenChange(false)} disabled={submitting}>{t("security.cancel")}</Button>
                <Button type="submit" disabled={submitting}>
                  {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                  {t("security.updatePassword")}
                </Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}

function MfaEnrollmentControl() {
  const { t } = useLanguage();
  const { toast } = useToast();
  const { refreshMfa } = useAuth();
  const [open, setOpen] = useState(false);
  const [enrollment, setEnrollment] = useState<MfaEnrollment | null>(null);
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const startEnrollment = async () => {
    if (loading) return;
    setOpen(true);
    setLoading(true);
    setError("");
    try {
      setEnrollment(await enrollMfa());
    } catch (failure) {
      setError(describeAuthError(failure));
    } finally {
      setLoading(false);
    }
  };

  const closeAndCleanup = async () => {
    const pendingFactorId = enrollment?.factorId;
    setOpen(false);
    setEnrollment(null);
    setCode("");
    setError("");
    if (pendingFactorId) {
      try {
        await unenrollMfa(pendingFactorId);
      } catch {
        // A later enrollment attempt also removes stale unverified factors.
      }
    }
  };

  const submitVerification = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!enrollment || code.length !== 6 || loading) return;
    setLoading(true);
    setError("");
    try {
      await verifyMfa(enrollment.factorId, code);
      setEnrollment(null);
      await refreshMfa();
      setOpen(false);
      setCode("");
      toast({ title: t("security.mfaEnabled") });
    } catch (failure) {
      setError(describeAuthError(failure));
      setCode("");
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => void startEnrollment()} disabled={loading}>{t("security.enableMfa")}</Button>
      <Dialog open={open} onOpenChange={(nextOpen) => { if (!nextOpen) void closeAndCleanup(); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("security.enableMfa")}</DialogTitle>
            <DialogDescription>{t("security.mfaEnrollDescription")}</DialogDescription>
          </DialogHeader>

          {loading && !enrollment ? (
            <div className="flex justify-center py-8"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
          ) : enrollment ? (
            <form onSubmit={submitVerification} className="space-y-4">
              <img src={enrollment.qrCode} alt={t("security.mfaQrAlt")} className="mx-auto h-48 w-48 rounded bg-white p-2" />
              <div className="space-y-2">
                <Label>{t("security.manualSecret")}</Label>
                <div className="flex gap-2">
                  <Input value={enrollment.secret} readOnly className="font-mono text-xs" aria-label={t("security.manualSecret")} />
                  <Button type="button" variant="outline" size="icon" aria-label={t("security.copySecret")} onClick={() => void navigator.clipboard.writeText(enrollment.secret)}>
                    <Copy className="h-4 w-4" />
                  </Button>
                </div>
              </div>
              <div className="space-y-2">
                <Label htmlFor="mfa-enroll-code">{t("security.verificationCode")}</Label>
                <InputOTP id="mfa-enroll-code" maxLength={6} value={code} onChange={setCode} disabled={loading} inputMode="numeric" autoComplete="one-time-code">
                  <InputOTPGroup>
                    {Array.from({ length: 6 }, (_, index) => <InputOTPSlot key={index} index={index} />)}
                  </InputOTPGroup>
                </InputOTP>
              </div>
              {error ? <p className="text-sm text-destructive" role="alert">{error}</p> : null}
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => void closeAndCleanup()} disabled={loading}>{t("security.cancel")}</Button>
                <Button type="submit" disabled={code.length !== 6 || loading}>
                  {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                  {t("security.enableMfa")}
                </Button>
              </DialogFooter>
            </form>
          ) : (
            <div className="space-y-4">
              <p className="text-sm text-destructive" role="alert">{error}</p>
              <DialogFooter><Button variant="outline" onClick={() => void closeAndCleanup()}>{t("security.close")}</Button></DialogFooter>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}

function MfaManagementControl({ factors }: { factors: MfaFactor[] }) {
  const { t } = useLanguage();
  const { toast } = useToast();
  const { refreshMfa } = useAuth();
  const [open, setOpen] = useState(false);
  const [pendingFactor, setPendingFactor] = useState<MfaFactor | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const removeFactor = async (event: React.MouseEvent) => {
    event.preventDefault();
    if (!pendingFactor || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      await unenrollMfa(pendingFactor.id);
      await refreshMfa();
      setPendingFactor(null);
      toast({ title: t("security.mfaDisabled") });
    } catch (failure) {
      setError(describeAuthError(failure));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>{t("security.manageMfa")}</Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("security.manageMfa")}</DialogTitle>
            <DialogDescription>{t("security.manageMfaDescription")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            {factors.map((factor, index) => (
              <div key={factor.id} className="flex items-center justify-between rounded-md border p-3">
                <div>
                  <p className="text-sm font-medium">{factor.friendlyName || `${t("security.authenticatorApp")} ${index + 1}`}</p>
                  <p className="text-xs text-muted-foreground">{new Date(factor.createdAt).toLocaleDateString()}</p>
                </div>
                <Button variant="destructive" size="sm" onClick={() => setPendingFactor(factor)}>{t("security.disable")}</Button>
              </div>
            ))}
          </div>
          {error ? <p className="text-sm text-destructive" role="alert">{error}</p> : null}
        </DialogContent>
      </Dialog>

      <AlertDialog open={Boolean(pendingFactor)} onOpenChange={(nextOpen) => { if (!nextOpen && !submitting) setPendingFactor(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("security.disableMfaTitle")}</AlertDialogTitle>
            <AlertDialogDescription>{t("security.disableMfaDescription")}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={submitting}>{t("security.cancel")}</AlertDialogCancel>
            <AlertDialogAction className="bg-destructive text-destructive-foreground hover:bg-destructive/90" disabled={submitting} onClick={removeFactor}>
              {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              {t("security.disable")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

function DeleteAccountControl() {
  const { t } = useLanguage();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const email = user?.email || "";
  const matches = Boolean(email) && confirmation.trim().toLowerCase() === email.toLowerCase();

  const handleOpenChange = (nextOpen: boolean) => {
    if (submitting) return;
    setOpen(nextOpen);
    if (!nextOpen) {
      setConfirmation("");
      setError("");
    }
  };

  const handleDelete = async (event: React.MouseEvent) => {
    event.preventDefault();
    if (!matches || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      await deleteAccount(email);
      localStorage.removeItem("slm_engine_meta");
      clearAppliedTuningRuns();
      await clearLocalAuthSession().catch(() => undefined);
      navigate("/login", { replace: true });
    } catch (failure) {
      setError(describeAuthError(failure));
      setSubmitting(false);
    }
  };

  return (
    <>
      <Button variant="destructive" size="sm" onClick={() => setOpen(true)}>{t("security.delete")}</Button>
      <AlertDialog open={open} onOpenChange={handleOpenChange}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("security.deleteAccountTitle")}</AlertDialogTitle>
            <AlertDialogDescription>{t("security.deleteAccountWarning")}</AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-3">
            <p className="text-sm">{t("security.deleteAccountPrompt")}</p>
            <code className="block rounded bg-muted px-3 py-2 text-xs">{email}</code>
            <Label htmlFor="delete-account-confirmation">{t("security.emailConfirmation")}</Label>
            <Input id="delete-account-confirmation" type="email" autoComplete="off" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} disabled={submitting} />
            <p className="text-xs text-muted-foreground">{t("security.engineDataNotDeleted")}</p>
            {error ? <p className="text-sm text-destructive" role="alert">{error}</p> : null}
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={submitting}>{t("security.cancel")}</AlertDialogCancel>
            <AlertDialogAction className="bg-destructive text-destructive-foreground hover:bg-destructive/90" disabled={!matches || submitting} onClick={handleDelete}>
              {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              {t("security.deletePermanently")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

export function AccountSecurity() {
  const { t } = useLanguage();
  const { mfaFactors, mfaLoading } = useAuth();

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg flex items-center gap-2">
          <Shield className="h-5 w-5 text-primary" /> {t("security.title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-sm font-medium">{t("security.password")}</p>
            <p className="text-xs text-muted-foreground">{t("security.passwordDescription")}</p>
          </div>
          <ChangePasswordControl />
        </div>
        <Separator />
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-sm font-medium">{t("security.mfa")}</p>
            <p className="text-xs text-muted-foreground">
              {mfaFactors.length > 0 ? t("security.mfaEnabledDescription") : t("security.mfaDescription")}
            </p>
          </div>
          {mfaLoading ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : (
            mfaFactors.length > 0 ? <MfaManagementControl factors={mfaFactors} /> : <MfaEnrollmentControl />
          )}
        </div>
        <Separator />
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-sm font-medium text-destructive">{t("security.deleteAccount")}</p>
            <p className="text-xs text-muted-foreground">{t("security.deleteAccountDescription")}</p>
          </div>
          <DeleteAccountControl />
        </div>
      </CardContent>
    </Card>
  );
}
