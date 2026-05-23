import ReactMarkdown from "react-markdown";
import { cn } from "@/lib/utils";

// Live preview of pasted markdown. Reads close to the .docx
// renderer's output (single column, plain typography).
export default function MarkdownPreview({ source, className }) {
  if (!source) {
    return (
      <div
        className={cn(
          "rounded-md border border-dashed border-border bg-muted/30 px-4 py-3 text-xs italic text-muted-foreground",
          "min-h-[280px]",
          className,
        )}
      >
        Live preview will appear here once you paste Claude's response.
      </div>
    );
  }
  return (
    <div
      className={cn(
        "markdown-preview rounded-md border border-border bg-muted/20 px-4 py-3 text-sm leading-relaxed",
        "min-h-[280px] max-h-[480px] overflow-auto",
        className,
      )}
    >
      <ReactMarkdown>{source}</ReactMarkdown>
    </div>
  );
}
