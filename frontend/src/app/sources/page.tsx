"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  type IngestRun,
  type Source,
  listIngestRuns,
  listSources,
  resetIndex,
  triggerIngest,
} from "@/lib/sources-api";

const POLL_INTERVAL_MS = 1500;

export default function SourcesPage() {
  const [sources, setSources] = useState<Source[]>([]);
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeRun, setActiveRun] = useState<IngestRun | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refreshSources = useCallback(async () => {
    try {
      const data = await listSources(includeDeleted);
      setSources(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load sources");
    }
  }, [includeDeleted]);

  useEffect(() => {
    setLoading(true);
    refreshSources().finally(() => setLoading(false));
  }, [refreshSources]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback(
    (runId: string) => {
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const runs = await listIngestRuns();
          const run = runs.find((r) => r.id === runId);
          if (run) {
            setActiveRun(run);
            if (run.status !== "running") {
              stopPolling();
              refreshSources();
            }
          }
        } catch {
          // ignore transient polling errors
        }
      }, POLL_INTERVAL_MS);
    },
    [stopPolling, refreshSources],
  );

  useEffect(() => stopPolling, [stopPolling]);

  const isRunning = activeRun?.status === "running";

  const handleReingest = async () => {
    setError(null);
    try {
      const { id } = await triggerIngest();
      setActiveRun({
        id,
        trigger: "api",
        status: "running",
        stats: {},
        error: null,
        started_at: new Date().toISOString(),
        finished_at: null,
      });
      startPolling(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to trigger ingest");
    }
  };

  const [resetDialogOpen, setResetDialogOpen] = useState(false);

  const handleReset = async () => {
    setError(null);
    try {
      await resetIndex();
      await refreshSources();
      setResetDialogOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to reset index");
    }
  };

  return (
    <main className="mx-auto flex max-w-4xl flex-col gap-4 p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">データ管理</h1>
        <div className="flex items-center gap-2">
          <Button onClick={handleReingest} disabled={isRunning}>
            {isRunning ? "再取り込み中..." : "再取り込み"}
          </Button>
          <AlertDialog open={resetDialogOpen} onOpenChange={setResetDialogOpen}>
            <AlertDialogTrigger render={<Button variant="destructive" />}>
              インデックス初期化
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>
                  インデックスを初期化しますか?
                </AlertDialogTitle>
                <AlertDialogDescription>
                  すべてのソースとチャンクが削除されます。会話履歴は保持されます。この操作は取り消せません。
                </AlertDialogDescription>
              </AlertDialogHeader>
              {error && <p className="text-sm text-destructive">{error}</p>}
              <AlertDialogFooter>
                <AlertDialogCancel>キャンセル</AlertDialogCancel>
                <AlertDialogAction onClick={handleReset}>
                  初期化する
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </div>

      {activeRun && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <span>実行状況:</span>
          <Badge
            variant={
              activeRun.status === "error" || activeRun.error
                ? "destructive"
                : "secondary"
            }
          >
            {activeRun.status}
          </Badge>
          {activeRun.stats && (
            <span>
              追加: {activeRun.stats.added ?? 0} / 更新:{" "}
              {activeRun.stats.updated ?? 0} / 削除:{" "}
              {activeRun.stats.deleted ?? 0} / スキップ:{" "}
              {activeRun.stats.skipped ?? 0}
            </span>
          )}
          {activeRun.error && (
            <span className="text-destructive">{activeRun.error}</span>
          )}
        </div>
      )}

      {error && <p className="text-sm text-destructive">{error}</p>}

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={includeDeleted}
          onChange={(e) => setIncludeDeleted(e.target.checked)}
        />
        削除済みも表示する
      </label>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>path</TableHead>
            <TableHead>title</TableHead>
            <TableHead>チャンク数</TableHead>
            <TableHead>最終取り込み日時</TableHead>
            <TableHead>状態</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading && (
            <TableRow>
              <TableCell colSpan={5}>読み込み中...</TableCell>
            </TableRow>
          )}
          {!loading && sources.length === 0 && (
            <TableRow>
              <TableCell colSpan={5}>ソースがありません</TableCell>
            </TableRow>
          )}
          {sources.map((source) => (
            <TableRow key={source.id}>
              <TableCell>{source.path}</TableCell>
              <TableCell>{source.title}</TableCell>
              <TableCell>{source.chunk_count}</TableCell>
              <TableCell>
                {new Date(source.updated_at).toLocaleString()}
              </TableCell>
              <TableCell>
                {source.deleted_at ? (
                  <Badge variant="secondary">削除済み</Badge>
                ) : (
                  "-"
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </main>
  );
}
