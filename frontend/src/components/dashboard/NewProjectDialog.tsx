import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Plus } from "lucide-react";
import { useLanguage } from "@/i18n/LanguageContext";

export function NewProjectDialog() {
  const { t } = useLanguage();
  return (
    <Button className="gap-2" asChild>
      <Link to="/projects/new" aria-label={t("command.newProject")}>
        <Plus className="h-4 w-4" /> {t("command.newProject")}
      </Link>
    </Button>
  );
}
