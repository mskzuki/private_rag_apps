import type { FC } from "react";
import { useMessage } from "@assistant-ui/react";
import { ExternalLink } from "lucide-react";

type Citation = {
  n: number;
  path?: string;
  title: string;
  heading: string;
};

export const Citations: FC = () => {
  const content = useMessage((m) => m.content[0]);
  const metadata = useMessage((m) => m.metadata) as
    | { custom?: { citations?: Citation[] } }
    | undefined;
  const isDone = !useMessage((m) => m.status?.type === "running");
  const citations = metadata?.custom?.citations || [];

  if (citations.length === 0) {
    return null;
  }

  const textContent = content?.type === "text" ? content.text : "";

  // The logic for filtering citations:
  // If we are done, only show citations that actually appeared as [n] in the text.
  // We ignore out-of-bounds citations (e.g., [9] if only 8 citations exist).
  const validCitationIndices = new Set<number>();

  if (isDone) {
    const matches = textContent.match(/\[(\d+)\]/g);
    if (matches) {
      matches.forEach((match) => {
        const idx = parseInt(match.replace(/\[|\]/g, ""), 10);
        // Only consider it valid if it corresponds to an actual citation
        if (citations.find((c: Citation) => c.n === idx)) {
          validCitationIndices.add(idx);
        }
      });
    }
  }

  const citationsToShow = isDone
    ? citations.filter((c: Citation) => validCitationIndices.has(c.n))
    : citations;

  if (citationsToShow.length === 0) {
    return null;
  }

  return (
    <div className="mt-4 flex flex-wrap gap-2">
      {citationsToShow.map((c: Citation) => (
        <a
          key={c.n}
          href={c.path ? `file://${c.path}` : "#"}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-1 rounded-lg border border-gray-200 bg-gray-100 px-3 py-1.5 text-xs transition-colors hover:bg-gray-200 dark:border-gray-700 dark:bg-gray-800 dark:hover:bg-gray-700"
          title={`${c.title} - ${c.heading}`}
        >
          <span className="font-semibold text-blue-600 dark:text-blue-400">
            [{c.n}]
          </span>
          <span className="max-w-[150px] truncate text-gray-700 dark:text-gray-300">
            {c.title}
          </span>
          <ExternalLink size={12} className="text-gray-400" />
        </a>
      ))}
    </div>
  );
};
