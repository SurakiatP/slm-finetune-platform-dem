-- ============================================
-- 1. trained_models
-- ============================================
CREATE TABLE public.trained_models (
  id uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id uuid NOT NULL,
  project_id uuid REFERENCES public.projects(id) ON DELETE CASCADE,
  name text NOT NULL,
  base_model text NOT NULL,
  task_type text NOT NULL,
  accuracy numeric NOT NULL DEFAULT 0,
  f1_score numeric NOT NULL DEFAULT 0,
  precision numeric NOT NULL DEFAULT 0,
  recall numeric NOT NULL DEFAULT 0,
  latency_ms integer NOT NULL DEFAULT 0,
  file_size text NOT NULL DEFAULT '0 MB',
  format text NOT NULL DEFAULT 'gguf',
  status text NOT NULL DEFAULT 'ready',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.trained_models ENABLE ROW LEVEL SECURITY;
CREATE POLICY "view own models" ON public.trained_models FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "insert own models" ON public.trained_models FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "update own models" ON public.trained_models FOR UPDATE USING (auth.uid() = user_id);
CREATE POLICY "delete own models" ON public.trained_models FOR DELETE USING (auth.uid() = user_id);
CREATE TRIGGER trained_models_updated BEFORE UPDATE ON public.trained_models FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();
CREATE INDEX idx_trained_models_user ON public.trained_models(user_id);

-- ============================================
-- 2. datasets
-- ============================================
CREATE TABLE public.datasets (
  id uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id uuid NOT NULL,
  project_id uuid REFERENCES public.projects(id) ON DELETE SET NULL,
  name text NOT NULL,
  description text NOT NULL DEFAULT '',
  rows integer NOT NULL DEFAULT 0,
  columns integer NOT NULL DEFAULT 0,
  file_size text NOT NULL DEFAULT '0 KB',
  format text NOT NULL DEFAULT 'jsonl',
  quality_score numeric NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.datasets ENABLE ROW LEVEL SECURITY;
CREATE POLICY "view own datasets" ON public.datasets FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "insert own datasets" ON public.datasets FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "update own datasets" ON public.datasets FOR UPDATE USING (auth.uid() = user_id);
CREATE POLICY "delete own datasets" ON public.datasets FOR DELETE USING (auth.uid() = user_id);
CREATE TRIGGER datasets_updated BEFORE UPDATE ON public.datasets FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();
CREATE INDEX idx_datasets_user ON public.datasets(user_id);

-- ============================================
-- 3. deployed_endpoints
-- ============================================
CREATE TABLE public.deployed_endpoints (
  id uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id uuid NOT NULL,
  model_id uuid REFERENCES public.trained_models(id) ON DELETE CASCADE,
  model_name text NOT NULL,
  project_name text NOT NULL DEFAULT '',
  endpoint_url text NOT NULL,
  status text NOT NULL DEFAULT 'inactive',
  requests_per_min integer NOT NULL DEFAULT 0,
  avg_latency_ms integer NOT NULL DEFAULT 0,
  error_rate numeric NOT NULL DEFAULT 0,
  uptime numeric NOT NULL DEFAULT 100,
  rate_limit_per_min integer NOT NULL DEFAULT 1000,
  burst_limit integer NOT NULL DEFAULT 50,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.deployed_endpoints ENABLE ROW LEVEL SECURITY;
CREATE POLICY "view own endpoints" ON public.deployed_endpoints FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "insert own endpoints" ON public.deployed_endpoints FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "update own endpoints" ON public.deployed_endpoints FOR UPDATE USING (auth.uid() = user_id);
CREATE POLICY "delete own endpoints" ON public.deployed_endpoints FOR DELETE USING (auth.uid() = user_id);
CREATE TRIGGER deployed_endpoints_updated BEFORE UPDATE ON public.deployed_endpoints FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();
CREATE INDEX idx_deployed_endpoints_user ON public.deployed_endpoints(user_id);

-- ============================================
-- 4. api_keys
-- ============================================
CREATE TABLE public.api_keys (
  id uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id uuid NOT NULL,
  name text NOT NULL,
  key_prefix text NOT NULL,
  key_suffix text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'active',
  last_used_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.api_keys ENABLE ROW LEVEL SECURITY;
CREATE POLICY "view own api keys" ON public.api_keys FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "insert own api keys" ON public.api_keys FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "update own api keys" ON public.api_keys FOR UPDATE USING (auth.uid() = user_id);
CREATE POLICY "delete own api keys" ON public.api_keys FOR DELETE USING (auth.uid() = user_id);
CREATE TRIGGER api_keys_updated BEFORE UPDATE ON public.api_keys FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();
CREATE INDEX idx_api_keys_user ON public.api_keys(user_id);

-- ============================================
-- 5. api_call_events (for analytics)
-- ============================================
CREATE TABLE public.api_call_events (
  id uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id uuid NOT NULL,
  endpoint text NOT NULL,
  status_code integer NOT NULL DEFAULT 200,
  latency_ms integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.api_call_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY "view own events" ON public.api_call_events FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "insert own events" ON public.api_call_events FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "delete own events" ON public.api_call_events FOR DELETE USING (auth.uid() = user_id);
CREATE INDEX idx_api_call_events_user_created ON public.api_call_events(user_id, created_at DESC);

-- ============================================
-- 6. Seed function for new users
-- ============================================
CREATE OR REPLACE FUNCTION public.seed_user_demo_data(_user_id uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  proj_id uuid;
  model_id uuid;
  i integer;
BEGIN
  -- Demo project
  INSERT INTO public.projects (user_id, name, description, task_type, base_model, status, progress, epochs, learning_rate, dataset_size, credits_cost, tags)
  VALUES (_user_id, 'Welcome — Sample Classifier', 'Demo project to explore the platform. Feel free to delete.', 'classification', 'qwen2.5-1.5b', 'completed', 100, 5, 0.0002, 1500, 80, ARRAY['demo'])
  RETURNING id INTO proj_id;

  -- Demo model
  INSERT INTO public.trained_models (user_id, project_id, name, base_model, task_type, accuracy, f1_score, precision, recall, latency_ms, file_size, format, status)
  VALUES (_user_id, proj_id, 'sample-classifier-v1', 'qwen2.5-1.5b', 'classification', 92.4, 91.8, 92.1, 91.5, 145, '850 MB', 'gguf', 'ready')
  RETURNING id INTO model_id;

  -- Demo dataset
  INSERT INTO public.datasets (user_id, project_id, name, description, rows, columns, file_size, format, quality_score)
  VALUES (_user_id, proj_id, 'sample_dataset.jsonl', 'Sample training data', 1500, 3, '420 KB', 'jsonl', 87.5);

  -- Demo endpoint
  INSERT INTO public.deployed_endpoints (user_id, model_id, model_name, project_name, endpoint_url, status, requests_per_min, avg_latency_ms, error_rate, uptime, rate_limit_per_min, burst_limit)
  VALUES (_user_id, model_id, 'sample-classifier-v1', 'Welcome — Sample Classifier', 'https://api.slmstudio.ai/v1/' || substring(_user_id::text, 1, 8) || '/sample', 'active', 24, 145, 0.3, 99.8, 1000, 50);

  -- Demo API key
  INSERT INTO public.api_keys (user_id, name, key_prefix, key_suffix, status)
  VALUES (_user_id, 'Default', 'sk-slm-' || substring(_user_id::text, 1, 4), substring(md5(_user_id::text), 1, 4), 'active');

  -- Sample API call events (last 7 days)
  FOR i IN 1..200 LOOP
    INSERT INTO public.api_call_events (user_id, endpoint, status_code, latency_ms, created_at)
    VALUES (
      _user_id,
      (ARRAY['/v1/chat/completions', '/v1/embeddings', '/v1/models', '/v1/completions'])[1 + (random() * 3)::int],
      CASE WHEN random() < 0.97 THEN 200 ELSE (ARRAY[400, 429, 500])[1 + (random() * 2)::int] END,
      80 + (random() * 200)::int,
      now() - (random() * interval '7 days')
    );
  END LOOP;
END;
$$;

-- ============================================
-- 7. Update handle_new_user to also seed demo data
-- ============================================
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  INSERT INTO public.profiles (user_id, display_name, avatar_url)
  VALUES (
    NEW.id,
    COALESCE(NEW.raw_user_meta_data ->> 'display_name', NEW.raw_user_meta_data ->> 'full_name', split_part(NEW.email, '@', 1)),
    NEW.raw_user_meta_data ->> 'avatar_url'
  );
  PERFORM public.seed_user_demo_data(NEW.id);
  RETURN NEW;
END;
$$;

-- Ensure trigger exists on auth.users
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- Seed demo data for existing users that don't have any projects yet
DO $$
DECLARE
  u record;
BEGIN
  FOR u IN SELECT id FROM auth.users WHERE id NOT IN (SELECT DISTINCT user_id FROM public.projects)
  LOOP
    PERFORM public.seed_user_demo_data(u.id);
  END LOOP;
END $$;