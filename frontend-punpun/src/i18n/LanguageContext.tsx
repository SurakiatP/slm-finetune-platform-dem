import { createContext, useContext, useState, useCallback, type ReactNode } from "react";
import { translations, type Language } from "./translations";

interface LanguageContextValue {
  language: Language;
  setLanguage: (lang: Language) => void;
  t: (key: string) => string;
}

const LanguageContext = createContext<LanguageContextValue | undefined>(undefined);

function getInitialLanguage(): Language {
  try {
    const stored = localStorage.getItem("app-language");
    if (stored === "th" || stored === "en") return stored;
  } catch {
    // localStorage unavailable — fall through to default
  }
  return "th";
}

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<Language>(getInitialLanguage);

  const setLanguage = useCallback((lang: Language) => {
    setLanguageState(lang);
    try {
      localStorage.setItem("app-language", lang);
    } catch {
      // ignore persistence failures
    }
  }, []);

  const t = useCallback(
    (key: string): string => translations[language][key] ?? key,
    [language]
  );

  return (
    <LanguageContext.Provider value={{ language, setLanguage, t }}>
      {children}
    </LanguageContext.Provider>
  );
}

export function useLanguage() {
  const ctx = useContext(LanguageContext);
  if (!ctx) throw new Error("useLanguage must be used within LanguageProvider");
  return ctx;
}
