import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const statusClassName: Record<string, string> = {
  blocked: "border-amber-400/40 bg-amber-500/10 text-amber-800 dark:text-amber-300",
  cancelled: "border-border bg-muted text-muted-foreground",
  completed:
    "border-emerald-500/35 bg-emerald-500/10 text-emerald-800 dark:text-emerald-300",
  failed: "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300",
  queued: "border-ring/40 bg-ring/10 text-primary dark:text-ring",
  running: "border-ring/40 bg-ring/10 text-primary dark:text-ring",
};

export function StatusPill({
  status,
  className,
}: {
  status: string | null | undefined;
  className?: string;
}) {
  const label = status ?? "unknown";
  return (
    <Badge
      className={cn(
        "together-mono-label h-6 rounded-[4px] border px-2 text-[10px]",
        statusClassName[label] ??
          "border-border bg-muted text-muted-foreground",
        className
      )}
      variant="outline"
    >
      {label}
    </Badge>
  );
}
