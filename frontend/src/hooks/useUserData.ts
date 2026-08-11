import { useEffect, useState, useCallback } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { listModels, type TrainedModelExt } from "@/lib/modelsApi";
import { listDatasets, type UserDataset } from "@/lib/datasetsApi";
import { listEndpoints, type DeployedEndpoint } from "@/lib/deploymentsApi";
import { listApiKeys, type ApiKey } from "@/lib/apiKeysApi";
import { listCallEvents, type CallEvent } from "@/lib/analyticsApi";

export function useModels() {
  const { user } = useAuth();
  const [models, setModels] = useState<TrainedModelExt[]>([]);
  const [loading, setLoading] = useState(true);
  const refresh = useCallback(async () => {
    if (!user) { setModels([]); setLoading(false); return; }
    setLoading(true);
    try { setModels(await listModels()); } finally { setLoading(false); }
  }, [user]);
  useEffect(() => { refresh(); }, [refresh]);
  return { models, loading, refresh };
}

export function useDatasets() {
  const { user } = useAuth();
  const [datasets, setDatasets] = useState<UserDataset[]>([]);
  const [loading, setLoading] = useState(true);
  const refresh = useCallback(async () => {
    if (!user) { setDatasets([]); setLoading(false); return; }
    setLoading(true);
    try { setDatasets(await listDatasets()); } finally { setLoading(false); }
  }, [user]);
  useEffect(() => { refresh(); }, [refresh]);
  return { datasets, loading, refresh };
}

export function useEndpoints() {
  const { user } = useAuth();
  const [endpoints, setEndpoints] = useState<DeployedEndpoint[]>([]);
  const [loading, setLoading] = useState(true);
  const refresh = useCallback(async () => {
    if (!user) { setEndpoints([]); setLoading(false); return; }
    setLoading(true);
    try { setEndpoints(await listEndpoints()); } finally { setLoading(false); }
  }, [user]);
  useEffect(() => { refresh(); }, [refresh]);
  return { endpoints, loading, refresh, setEndpoints };
}

export function useApiKeys() {
  const { user } = useAuth();
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const refresh = useCallback(async () => {
    if (!user) { setKeys([]); setLoading(false); return; }
    setLoading(true);
    try { setKeys(await listApiKeys()); } finally { setLoading(false); }
  }, [user]);
  useEffect(() => { refresh(); }, [refresh]);
  return { keys, loading, refresh, setKeys };
}

export function useCallEvents(range: string) {
  const { user } = useAuth();
  const [events, setEvents] = useState<CallEvent[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    if (!user) { setEvents([]); setLoading(false); return; }
    setLoading(true);
    listCallEvents(range).then(setEvents).finally(() => setLoading(false));
  }, [user, range]);
  return { events, loading };
}
