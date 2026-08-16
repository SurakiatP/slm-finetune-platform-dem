import { useLanguage } from "@/i18n/LanguageContext";
import { Button } from "@/components/ui/button";
import { Globe } from "lucide-react";

export function LanguageSwitcher() {
  const { language, setLanguage } = useLanguage();

  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={() => setLanguage(language === "en" ? "th" : "en")}
      className="h-8 px-2 gap-1.5 text-xs font-medium"
    >
      <Globe className="h-3.5 w-3.5" />
      {language === "en" ? "TH" : "EN"}
    </Button>
  );
}
