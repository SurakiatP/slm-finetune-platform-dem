import { createContext, useCallback, useContext, useEffect, useState, ReactNode } from "react";
import { Session, User } from "@supabase/supabase-js";
import { supabase } from "@/integrations/supabase/client";
import {
  getMfaAssuranceLevel,
  listMfaFactors,
  type MfaFactor,
} from "@/lib/accountSecurity";

interface Profile {
  id: string;
  user_id: string;
  display_name: string | null;
  avatar_url: string | null;
}

interface AuthContextValue {
  session: Session | null;
  user: User | null;
  profile: Profile | null;
  loading: boolean;
  mfaLoading: boolean;
  mfaError: string | null;
  mfaFactors: MfaFactor[];
  currentAal: string | null;
  nextAal: string | null;
  mfaRequired: boolean;
  signOut: () => Promise<void>;
  refreshProfile: () => Promise<void>;
  refreshMfa: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const [mfaLoading, setMfaLoading] = useState(true);
  const [mfaError, setMfaError] = useState<string | null>(null);
  const [mfaFactors, setMfaFactors] = useState<MfaFactor[]>([]);
  const [currentAal, setCurrentAal] = useState<string | null>(null);
  const [nextAal, setNextAal] = useState<string | null>(null);

  const loadProfile = useCallback(async (userId: string) => {
    const { data } = await supabase
      .from("profiles")
      .select("id, user_id, display_name, avatar_url")
      .eq("user_id", userId)
      .maybeSingle();
    setProfile(data ?? null);
  }, []);

  const refreshMfa = useCallback(async () => {
    setMfaLoading(true);
    setMfaError(null);
    try {
      const [factors, assurance] = await Promise.all([
        listMfaFactors(),
        getMfaAssuranceLevel(),
      ]);
      setMfaFactors(factors);
      setCurrentAal(assurance.currentLevel);
      setNextAal(assurance.nextLevel);
    } catch (error) {
      setMfaError(error instanceof Error ? error.message : "Unable to verify two-factor authentication status.");
      throw error;
    } finally {
      setMfaLoading(false);
    }
  }, []);

  const resetMfa = useCallback(() => {
    setMfaFactors([]);
    setCurrentAal(null);
    setNextAal(null);
    setMfaError(null);
    setMfaLoading(false);
  }, []);

  useEffect(() => {
    // 1) Subscribe FIRST
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, newSession) => {
      setSession(newSession);
      setUser(newSession?.user ?? null);
      if (newSession?.user) {
        // Defer DB call to avoid recursive lock with auth state
        setMfaLoading(true);
        setTimeout(() => {
          void Promise.all([loadProfile(newSession.user.id), refreshMfa()]).catch(() => undefined);
        }, 0);
      } else {
        setProfile(null);
        resetMfa();
      }
    });

    // 2) Then check existing session
    void supabase.auth.getSession().then(async ({ data: { session: existing } }) => {
      setSession(existing);
      setUser(existing?.user ?? null);
      if (existing?.user) {
        await Promise.all([loadProfile(existing.user.id), refreshMfa()]).catch(() => undefined);
      } else {
        resetMfa();
      }
    }).catch(() => {
      resetMfa();
    }).finally(() => {
      setLoading(false);
    });

    return () => subscription.unsubscribe();
  }, [loadProfile, refreshMfa, resetMfa]);

  const signOut = async () => {
    await supabase.auth.signOut();
    setProfile(null);
  };

  const refreshProfile = async () => {
    if (user) await loadProfile(user.id);
  };

  const mfaRequired = nextAal === "aal2" && currentAal !== "aal2";

  return (
    <AuthContext.Provider value={{
      session,
      user,
      profile,
      loading,
      mfaLoading,
      mfaError,
      mfaFactors,
      currentAal,
      nextAal,
      mfaRequired,
      signOut,
      refreshProfile,
      refreshMfa,
    }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
