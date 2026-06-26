"use client";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { ArrowUp, Sparkles } from "lucide-react";
import { type ReactNode, useState } from "react";

export type StrategyStartPromptSuggestion = {
  label: string;
  disabled?: boolean;
  onSelect: () => void;
};

const strategyPromptTextareaClassName =
  "min-h-20 resize-none rounded-md border-0 !bg-muted/35 px-2 py-2 shadow-none focus-visible:ring-0 dark:!bg-muted/35";

const strategyPromptInputShellClassName =
  "relative h-auto rounded-[8px] border-border !bg-card p-2 pb-12 shadow-sm dark:!bg-card";

const strategyPromptActionRowClassName =
  "absolute inset-x-2 bottom-2 flex items-center justify-between gap-2 p-0";

type StrategyComposerRenderState = {
  submitDisabled: boolean;
};

export function StrategyComposer({
  className,
  disabled = false,
  endAction,
  onSubmit,
  onValueChange,
  placeholder,
  requireText = false,
  startAction,
  value,
}: {
  className?: string;
  disabled?: boolean;
  endAction: (state: StrategyComposerRenderState) => ReactNode;
  onSubmit: (text: string) => void | Promise<void>;
  onValueChange: (value: string) => void;
  placeholder: string;
  requireText?: boolean;
  startAction?: ReactNode;
  value: string;
}) {
  const submitDisabled = disabled || (requireText && !value.trim());
  const submit = async () => {
    if (submitDisabled) {
      return;
    }
    await onSubmit(value.trim());
  };

  return (
    <form
      className={cn("w-full", className)}
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <div className={cn("w-full border", strategyPromptInputShellClassName)}>
        <Textarea
          className={strategyPromptTextareaClassName}
          data-strategy-composer-input
          disabled={disabled}
          onChange={(event) => onValueChange(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void submit();
            }
          }}
          placeholder={placeholder}
          value={value}
        />
        <div className={cn("flex", strategyPromptActionRowClassName)}>
          <div className="min-w-0">{startAction}</div>
          <div className="flex shrink-0 items-center gap-2">
            {endAction({ submitDisabled })}
          </div>
        </div>
      </div>
    </form>
  );
}

export function StrategyStartPrompt({
  className,
  disabled = false,
  onSubmit,
  placeholder,
  requireText = false,
  startAction,
  submitLabel,
  suggestions,
  status,
  title,
}: {
  className?: string;
  disabled?: boolean;
  onSubmit: (text: string) => void | Promise<void>;
  placeholder: string;
  requireText?: boolean;
  startAction?: ReactNode;
  submitLabel: string;
  suggestions: StrategyStartPromptSuggestion[];
  status?: ReactNode;
  title: string;
}) {
  const [value, setValue] = useState("");

  return (
    <section
      className={cn(
        "mx-auto flex w-full max-w-3xl flex-col items-center justify-center px-4 text-center",
        className
      )}
    >
      <p className="font-semibold text-2xl tracking-[-0.03em]">{title}</p>

      <div className="mt-5 flex max-w-2xl flex-wrap justify-center gap-2">
        {suggestions.map((suggestion) => (
          <button
            className="inline-flex max-w-full items-center gap-2 rounded-full border border-border bg-muted/40 px-3 py-1.5 text-muted-foreground text-xs transition hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
            disabled={disabled || suggestion.disabled}
            key={suggestion.label}
            onClick={suggestion.onSelect}
            type="button"
          >
            <Sparkles className="size-3 shrink-0" />
            <span className="truncate">{suggestion.label}</span>
          </button>
        ))}
      </div>

      <StrategyComposer
        className="mt-5"
        disabled={disabled}
        endAction={({ submitDisabled }) => (
          <Button
            aria-label={submitLabel}
            className="rounded-full"
            disabled={submitDisabled}
            size="icon-sm"
            type="submit"
          >
            <ArrowUp className="size-4" />
          </Button>
        )}
        onSubmit={async (text) => {
          setValue("");
          await onSubmit(text);
        }}
        onValueChange={setValue}
        placeholder={placeholder}
        requireText={requireText}
        startAction={startAction}
        value={value}
      />
      {status ? <div className="mt-4 w-full text-left">{status}</div> : null}
    </section>
  );
}
