export interface Principal {
  id: string;
  kind: "user" | "agent" | "service";
  display_name: string;
  workspace_id: string;
  token_hint: string;
  dev_token: string | null;
}

export interface Service {
  id: string;
  slug: string;
  name: string;
  visibility: string;
}

export interface Endpoint {
  id: string;
  service_id: string;
  environment: string;
  base_url: string;
  approved_hosts: string[];
  status: string;
}

export interface Operation {
  id: string;
  service_id: string;
  method: string;
  normalized_path: string;
  status: string;
}

export interface CapabilityVersion {
  id: string;
  version: string;
  risk_level: string;
  status: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  allow_fields: string[];
  execution: Record<string, unknown>;
}

export interface Capability {
  id: string;
  name: string;
  display_name: string;
  description: string;
  status: string;
  versions: CapabilityVersion[];
}

export interface Connection {
  id: string;
  service_id: string;
  auth_scheme: string;
  scopes: string[];
  status: string;
  secret_configured: boolean;
}

export interface Profile {
  id: string;
  service_id: string;
  endpoint_id: string;
  connection_id: string;
  name: string;
  status: string;
}

export interface ActiveBinding {
  service_id: string;
  context_key: string;
  active_profile_id: string;
  display_label: string;
  version: number;
}

export interface InvocationView {
  id: string;
  capability: string;
  status: string;
  profile_label?: string;
  created_at: string;
  result?: Record<string, unknown>;
  error_code?: string;
}

export interface AuditEvent {
  id: string;
  action: string;
  decision: string;
  invocation_id: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface ImportSummary {
  job_id: string;
  state: string;
  candidates: number;
  operations_created: number;
  operations_updated: number;
  observations_created: number;
}

export interface CollectorPairing {
  id: string;
  service_id: string;
  status: string;
  created_at: string | null;
  pairing_expires_at: string;
  token_expires_at: string | null;
  last_submitted_at: string | null;
  observation_count: number;
  attempts: number;
  pairing_code?: string; // present only in the create response
}

export interface CollectorObservation {
  id: string;
  service_id: string;
  source: string;
  normalized_shape: {
    method?: string;
    template?: string;
    query_keys?: string[];
    sample_count?: number;
    status_codes?: number[];
  };
  created_at: string | null;
}

export interface OperationVersionView {
  id: string;
  version: number;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
}

export interface OperationDetail {
  id: string;
  method: string;
  normalized_path: string;
  status: string;
  versions: OperationVersionView[];
}

export interface OperationTestResult {
  ok: boolean;
  executor_status: number;
  error_code: string | null;
  result: unknown;
}

export interface CapabilityDetailView {
  id: string;
  name: string;
  display_name: string;
  description: string;
  status: string;
  versions: {
    id: string;
    version: string;
    risk_level: string;
    status: string;
    input_schema: Record<string, unknown>;
    output_schema: Record<string, unknown>;
    allow_fields: string[];
    execution: Record<string, unknown>;
  }[];
}
