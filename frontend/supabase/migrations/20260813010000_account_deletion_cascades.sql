-- Ensure deleting an Auth user removes every row owned by that user.
-- profiles and projects already have equivalent cascading foreign keys.

ALTER TABLE public.trained_models
  ADD CONSTRAINT trained_models_user_id_fkey
  FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;

ALTER TABLE public.datasets
  ADD CONSTRAINT datasets_user_id_fkey
  FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;

ALTER TABLE public.deployed_endpoints
  ADD CONSTRAINT deployed_endpoints_user_id_fkey
  FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;

ALTER TABLE public.api_keys
  ADD CONSTRAINT api_keys_user_id_fkey
  FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;

ALTER TABLE public.api_call_events
  ADD CONSTRAINT api_call_events_user_id_fkey
  FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;
