import type { FC } from "react";
import { useMessage } from "@assistant-ui/react";
import { BookOpen, Sparkles } from "lucide-react";

type Route = "grounded" | "direct";

/**
 * route バッジ（grounded / direct の2状態表示。M7 T6 最小実装）
 * docs/specs/m7_adaptive_routing.md rev.3 §5.2, タスクT6ブリーフ補足5
 *
 * Citations.tsx と同じパターン（useMessage((m) => m.metadata) で
 * metadata.custom.route を読む）。route は1メッセージにつき1回しか届かないため、
 * Citations のような isDone フィルタは不要で、出現したら常に表示する。
 * スコープ外: 進捗のリッチUI・補足セクション有無のバッジ表示（ブリーフ参照）
 */
export const RouteBadge: FC = () => {
  const metadata = useMessage((m) => m.metadata) as
    | { custom?: { route?: Route } }
    | undefined;
  const route = metadata?.custom?.route;

  if (!route) {
    return null;
  }

  const isGrounded = route === "grounded";

  return (
    <div className="mb-2 flex">
      <span
        className={
          isGrounded
            ? "inline-flex items-center gap-1 rounded-full border border-blue-200 bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-700 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-300"
            : "inline-flex items-center gap-1 rounded-full border border-gray-200 bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-700 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300"
        }
        title={
          isGrounded
            ? "コーパスの内容に基づく回答です"
            : "コーパスに関連する記述が見つからず、一般知識のみで回答しています"
        }
      >
        {isGrounded ? <BookOpen size={12} /> : <Sparkles size={12} />}
        {isGrounded ? "コーパス根拠あり" : "一般知識のみ"}
      </span>
    </div>
  );
};
