"use client";

import type { ComponentProps, ReactNode } from "react";
import {
  CheckIcon,
  ChevronDownIcon,
  CircleIcon,
  LoaderCircleIcon,
  type LucideIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";

export type ChainOfThoughtProps = ComponentProps<typeof Collapsible>;

export const ChainOfThought = ({
  className,
  defaultOpen = true,
  ...props
}: ChainOfThoughtProps) => (
  <Collapsible
    className={cn("not-prose rounded-[6px] border border-border/70 bg-muted/20", className)}
    defaultOpen={defaultOpen}
    {...props}
  />
);

export type ChainOfThoughtHeaderProps = ComponentProps<typeof CollapsibleTrigger> & {
  children?: ReactNode;
};

export const ChainOfThoughtHeader = ({
  children,
  className,
  ...props
}: ChainOfThoughtHeaderProps) => (
  <CollapsibleTrigger
    className={cn(
      "group grid w-full grid-cols-[1.25rem_minmax(0,1fr)_1rem] items-center gap-3 px-3 py-2 text-left text-muted-foreground text-sm transition-colors hover:text-foreground",
      className
    )}
    {...props}
  >
    <span aria-hidden="true" />
    <span className="min-w-0 truncate">{children}</span>
    <ChevronDownIcon className="size-4 shrink-0 transition-transform group-data-[state=open]:rotate-180" />
  </CollapsibleTrigger>
);

export type ChainOfThoughtContentProps = ComponentProps<typeof CollapsibleContent>;

export const ChainOfThoughtContent = ({
  className,
  ...props
}: ChainOfThoughtContentProps) => (
  <CollapsibleContent
    className={cn(
      "px-3 pb-3 data-[state=closed]:fade-out-0 data-[state=closed]:slide-out-to-top-1 data-[state=open]:slide-in-from-top-1 data-[state=closed]:animate-out data-[state=open]:animate-in",
      className
    )}
    {...props}
  />
);

export type ChainOfThoughtStepStatus = "complete" | "active" | "pending";

export type ChainOfThoughtStepProps = ComponentProps<"div"> & {
  icon?: LucideIcon;
  label?: string;
  description?: string;
  status?: ChainOfThoughtStepStatus;
};

const statusIcon = {
  active: LoaderCircleIcon,
  complete: CheckIcon,
  pending: CircleIcon,
} satisfies Record<ChainOfThoughtStepStatus, LucideIcon>;

export const ChainOfThoughtStep = ({
  children,
  className,
  description,
  icon,
  label,
  status = "pending",
  ...props
}: ChainOfThoughtStepProps) => {
  const Icon = icon ?? statusIcon[status];

  return (
    <div
      className={cn(
        "grid grid-cols-[1.25rem_minmax(0,1fr)] items-center gap-3 py-2 first:pt-0 last:pb-0",
        status === "pending" && "text-muted-foreground/70",
        className
      )}
      {...props}
    >
      <span
        className={cn(
          "flex size-5 shrink-0 items-center justify-center rounded-full border bg-background",
          status === "complete" && "border-emerald-500/40 text-emerald-400",
          status === "active" && "border-primary/50 text-primary",
          status === "pending" && "border-border text-muted-foreground"
        )}
      >
        <Icon className={cn("size-3", status === "active" && "animate-spin")} />
      </span>
      <div className="min-w-0 flex-1">
        {label ? (
          <p
            className={cn(
              "truncate font-medium text-sm",
              status === "active" ? "text-foreground" : "text-muted-foreground"
            )}
          >
            {label}
          </p>
        ) : null}
        {description ? (
          <p className="mt-0.5 text-muted-foreground text-xs">{description}</p>
        ) : null}
        {children ? <div className="mt-2 text-muted-foreground text-xs">{children}</div> : null}
      </div>
    </div>
  );
};

export type ChainOfThoughtSearchResultsProps = ComponentProps<"div">;

export const ChainOfThoughtSearchResults = ({
  className,
  ...props
}: ChainOfThoughtSearchResultsProps) => (
  <div className={cn("mt-2 flex flex-wrap gap-1.5", className)} {...props} />
);

export type ChainOfThoughtSearchResultProps = ComponentProps<typeof Badge>;

export const ChainOfThoughtSearchResult = ({
  className,
  variant = "secondary",
  ...props
}: ChainOfThoughtSearchResultProps) => (
  <Badge className={cn("max-w-full truncate", className)} variant={variant} {...props} />
);

export type ChainOfThoughtImageProps = ComponentProps<"div"> & {
  caption?: string;
};

export const ChainOfThoughtImage = ({
  caption,
  children,
  className,
  ...props
}: ChainOfThoughtImageProps) => (
  <div className={cn("mt-2 overflow-hidden rounded-[6px] border border-border", className)} {...props}>
    {children}
    {caption ? (
      <p className="border-border border-t bg-muted/30 px-2 py-1 text-muted-foreground text-xs">
        {caption}
      </p>
    ) : null}
  </div>
);
