/** Governed lineage manifest returned by GET /api/lineage/manifest. */
export type LineageLayer =
  | 'raw_share'
  | 'silver'
  | 'gold'
  | 'uc_function'
  | 'metric_view'
  | 'reference';

export interface LineageManifestNode {
  id: string;
  layer: LineageLayer;
  object_type: 'table' | 'view' | 'function';
  fqn: string;
  label: string;
  note?: string | null;
  catalog_explorer_url?: string | null;
}

export interface LineageManifestFamily {
  id: string;
  title: string;
  description: string;
  nodes: LineageManifestNode[];
}

export interface LineageManifestResponse {
  schema_version: number;
  manifest_path: string;
  families: LineageManifestFamily[];
}
