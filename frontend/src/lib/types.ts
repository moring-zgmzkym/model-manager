export type Status = "stopped" | "starting" | "running" | "stopping" | "error";

export interface Binding {
  protocol?: string;
  url?: string;
  command?: string;
  cwd?: string;
  port?: number;
  path?: string;
  api?: string;
  fields?: CustomField[];
  [key: string]: unknown;
}

export interface CustomField {
  name: string;
  label?: string;
  type?: "text" | "textarea" | "number" | "file" | "select";
  options?: string[];
  default?: string | number;
  accept?: string;
}

export interface ActionField {
  name: string;
  label?: string;
  type?: "text" | "textarea" | "number" | "select" | "image" | "file";
  default?: string | number;
  options?: string[];
  required?: boolean;
  help?: string;
  placeholder?: string;
  accept?: string;
}

export interface ActionPreset {
  id: string;
  label: string;
  values: Record<string, any>;
  note?: string;
}

/** A sub-feature of one capability, e.g. text-to-video vs first/last-frame. */
export interface ActionSpec {
  id: string;
  label: string;
  description?: string;
  fields?: ActionField[];
  defaults?: Record<string, any>;
  presets?: ActionPreset[];
}

export interface Capability {
  type: string;
  enabled: boolean;
  source: "manifest" | "files" | "runtime" | "user" | "plugin";
  confidence: number;
  evidence: string[];
  binding: Binding;
  label?: string | null;
  locked?: boolean;
  actions?: ActionSpec[];
}

export interface LaunchConfig {
  command: string;
  stop_command: string;
  cwd: string;
  python: string;
  env: Record<string, string>;
  host: string;
  port: number | null;
  health_path: string;
  startup_timeout: number;
  adopt_external: boolean;
  keep_running_on_exit: boolean;
}

export interface Runtime {
  key: string;
  status: Status;
  pid?: number | null;
  port?: number | null;
  started_at?: number | null;
  exit_code?: number | null;
  message?: string;
  external?: boolean;
  memory?: number | null;
}

export interface Model {
  id: string;
  name: string;
  path: string;
  adapter: string;
  description: string;
  tags: string[];
  color: string;
  launch: LaunchConfig;
  api_base: string;
  capabilities: Capability[];
  defaults: Record<string, any>;
  created_at: number;
  updated_at: number;
  base_url: string;
  runtime: Runtime;
  services: Record<string, Runtime>;
}

export interface DetectResult {
  path: string;
  exists: boolean;
  name: string;
  adapter: string;
  adapter_label: string;
  description: string;
  capabilities: Capability[];
  launch: LaunchConfig;
  scripts: string[];
  notes: string[];
  defaults: Record<string, any>;
  already_added?: boolean;
}

export interface CapabilityInfo {
  label: string;
  group: string;
  panel: string;
  protocol: string;
  desc: string;
}

export interface Catalog {
  capabilities: Record<string, CapabilityInfo>;
  protocols: Record<string, string>;
  adapters: Record<string, string>;
  plugins: { file: string; ok: boolean; name?: string; description?: string; error?: string }[];
}

export interface OutputItem {
  id: string;
  kind: "image" | "audio" | "video";
  model_id: string;
  model_name: string;
  capability: string;
  prompt: string;
  file: string;
  url: string;
  size: number;
  created_at: number;
  meta: Record<string, any>;
}

export interface LogLine {
  t: number;
  line: string;
}

export interface Settings {
  language: string;
  theme: "light" | "dark" | "system";
  port_range_start: number;
  default_python: string;
  model_roots: string[];
  stop_models_on_exit: boolean;
  data_dir?: string;
  python_executable?: string;
  platform?: string;
}

export interface SystemStats {
  cpu: number;
  cpu_count: number;
  memory: { total: number; used: number; percent: number };
  gpus: { name: string; memory_total: number; memory_used: number; utilization: number; temperature: number | null }[];
  disk: { total: number; used: number; free: number } | null;
  running_models: number;
}
