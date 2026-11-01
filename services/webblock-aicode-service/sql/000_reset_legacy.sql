-- One-time reset from the pre-ERD schema (tables in public) to the per-service schemas below.
-- Does nothing once the legacy tables are gone, so it is safe to re-run.
DO $$
BEGIN
  IF to_regclass('public.code_artifacts') IS NOT NULL OR to_regclass('public.customization_requests') IS NOT NULL THEN
    DROP TABLE IF EXISTS public.change_item_targets, public.ai_change_items, public.ai_change_plans,
      public.component_bindings, public.code_artifacts, public.deployments, public.harness_runs,
      public.customization_requests, public.puck_component_definitions, public.input_versions, public.projects CASCADE;
    DROP TYPE IF EXISTS public.harness_flow, public.harness_status, public.implementation_strategy, public.plan_status,
      public.change_type, public.change_operation, public.item_status, public.target_type, public.resolution_status;
  END IF;
END $$;
