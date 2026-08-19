import { useEffect, useState } from "react";
import { engineListBaseModels, type EngineBaseModelInfo } from "@/lib/engineApi";

export function useBaseModels() {
  const [models, setModels] = useState<EngineBaseModelInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    engineListBaseModels()
      .then((data) => {
        if (active) {
          setModels(data);
          setError(null);
        }
      })
      .catch((err) => {
        if (active) {
          setError(err instanceof Error ? err.message : "Failed to fetch base models");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  return { models, loading, error };
}
