export type Source = {
  id: string;
  path: string;
  title: string;
  chunk_count: number;
  updated_at: string;
  deleted_at: string | null;
};

export type IngestRun = {
  id: string;
  trigger: string;
  status: "running" | "success" | "error";
  stats: {
    added?: number;
    updated?: number;
    deleted?: number;
    skipped?: number;
    failed_files?: string[];
  };
  error: string | null;
  started_at: string;
  finished_at: string | null;
};

export async function listSources(includeDeleted: boolean): Promise<Source[]> {
  const res = await fetch(`/api/sources?include_deleted=${includeDeleted}`);
  if (!res.ok) throw new Error("Failed to fetch sources");
  return res.json();
}

export async function triggerIngest(): Promise<{ id: string }> {
  const res = await fetch("/api/ingest", { method: "POST" });
  if (res.status === 409) throw new Error("ingestion already running");
  if (!res.ok) throw new Error("Failed to trigger ingest");
  return res.json();
}

export async function listIngestRuns(): Promise<IngestRun[]> {
  const res = await fetch("/api/ingest/runs");
  if (!res.ok) throw new Error("Failed to fetch ingest runs");
  return res.json();
}

export async function resetIndex(): Promise<void> {
  const res = await fetch("/api/index", { method: "DELETE" });
  if (res.status === 409)
    throw new Error("cannot reset while ingestion is running");
  if (!res.ok) throw new Error("Failed to reset index");
}
