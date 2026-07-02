"use client";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  accountInitial,
  accountName,
  accountSubtitle,
  formatUsageCost,
  formatUsageNumber,
  providerDisplay,
  providerFallbackEnabled,
  providerRouteReady,
} from "@/features/account";
import {
  getMessageText,
  type StrategyChatMessage,
} from "@/features/chat/ui/chat-ui";
import type {
  AccountUsageResponse,
  Conversation as ChatConversation,
  ConversationSidebarItem,
  MeResponse,
  ProviderStatusResponse,
  ReadyResponse,
} from "@/lib/backend-schemas";
import {
  getUiCopy,
  languageLabel,
  languageLocale,
  type LanguagePreference as UiLanguagePreference,
} from "@/lib/i18n";
import { useI18n } from "@/lib/language";
import type { ResolvedTheme, ThemePreference } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { useClerk, useUser } from "@clerk/nextjs";
import Image from "next/image";
import type { ReactNode } from "react";
import {
  Bot,
  Building2,
  Check,
  ChevronsUpDown,
  CircleHelp,
  Clipboard,
  CreditCard,
  FileStack,
  Gauge,
  Globe2,
  LogOut,
  MessageSquarePlus,
  MonitorCog,
  MoreHorizontal,
  PanelLeft,
  PanelRight,
  Pencil,
  RefreshCcw,
  Search,
  Settings,
  Trash2,
} from "lucide-react";

export type AccountDialog = "settings" | "language" | "appearance" | "help";
export type SettingsTab = "general" | "provider" | "usage" | "workspace";

export function ConversationSidebar({
  accountUsage,
  activeView = "chat",
  collapsed,
  conversations,
  isCreating,
  isNewChatDisabled,
  isLoading,
  language,
  me,
  onCreate,
  onDelete,
  onLanguageChange,
  onOpenAccountDialog,
  onOpenSettingsTab,
  onOpenArtifacts,
  onOpenPaperBots,
  onRename,
  onSelect,
  onToggleCollapsed,
  onThemeChange,
  providerStatus,
  selectedConversationId,
  theme,
}: {
  accountUsage?: AccountUsageResponse;
  activeView?: "chat" | "artifacts" | "paper-bots";
  collapsed: boolean;
  conversations: ConversationSidebarItem[];
  isCreating: boolean;
  isNewChatDisabled: boolean;
  isLoading: boolean;
  language: UiLanguagePreference;
  me?: MeResponse;
  onCreate: () => void;
  onDelete: (conversation: ChatConversation) => void;
  onLanguageChange: (language: UiLanguagePreference) => void;
  onOpenAccountDialog: (dialog: AccountDialog) => void;
  onOpenSettingsTab: (tab: SettingsTab) => void;
  onOpenArtifacts: () => void;
  onOpenPaperBots: () => void;
  onRename: (conversation: ChatConversation) => void;
  onSelect: (conversationId: string) => void;
  onToggleCollapsed: () => void;
  onThemeChange: (theme: ThemePreference) => void;
  providerStatus?: ProviderStatusResponse;
  selectedConversationId: string | null;
  theme: ThemePreference;
}) {
  const t = getUiCopy(language);
  const artifactsLabel = t.artifacts;
  const paperBotsLabel = t.paperBots;
  return (
    <aside
      className={cn(
        "apple-frosted hidden shrink-0 overflow-hidden border-r border-sidebar-border text-sidebar-foreground transition-[width] duration-300 ease-out md:grid",
        collapsed ? "w-14" : "w-[288px]"
      )}
    >
      <div className="relative min-h-0">
        <div
          aria-hidden={!collapsed}
          className={cn(
            "absolute inset-0 flex flex-col items-center px-2 py-3 transition-[opacity,transform] duration-200 ease-out",
            collapsed
              ? "pointer-events-auto translate-x-0 opacity-100 delay-100"
              : "pointer-events-none -translate-x-2 opacity-0"
          )}
        >
          <button
            aria-label={t.expandSidebar}
            className="group flex size-9 items-center justify-center rounded-full transition hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
            onClick={onToggleCollapsed}
            title={t.expandSidebar}
            type="button"
          >
            <span className="block group-hover:hidden">
              <StrategyLogoMark compact />
            </span>
            <PanelRight className="hidden size-4 group-hover:block" />
          </button>
          <nav className="mt-5 flex flex-col items-center gap-3">
            <SidebarRailButton
              disabled={isCreating || isNewChatDisabled}
              icon={<MessageSquarePlus className="size-4" />}
              label={t.newChat}
              onClick={onCreate}
            />
            <SidebarRailButton
              icon={<Search className="size-4" />}
              label={t.searchChats}
            />
            <SidebarRailButton
              active={activeView === "artifacts"}
              icon={<FileStack className="size-4" />}
              label={artifactsLabel}
              onClick={onOpenArtifacts}
            />
            <SidebarRailButton
              active={activeView === "paper-bots"}
              icon={<Bot className="size-4" />}
              label={paperBotsLabel}
              onClick={onOpenPaperBots}
            />
            <SidebarRailButton
              disabled={!selectedConversationId}
              icon={<MessageSquarePlus className="size-4" />}
              label={activeConversationLabel(conversations, selectedConversationId, t)}
              onClick={() => {
                if (selectedConversationId) {
                  onSelect(selectedConversationId);
                }
              }}
            />
          </nav>
        </div>
        <div
          aria-hidden={collapsed}
          className={cn(
            "absolute inset-0 flex w-[288px] flex-col p-3 transition-[opacity,transform] duration-200 ease-out",
            collapsed
              ? "pointer-events-none translate-x-2 opacity-0"
              : "pointer-events-auto translate-x-0 opacity-100 delay-100"
          )}
        >
          <div className="flex items-center justify-between px-2 py-2">
            <StrategyLogoMark />
            <Button
                  className="text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              onClick={onToggleCollapsed}
              size="icon-sm"
              title={t.collapseSidebar}
              type="button"
              variant="ghost"
            >
              <PanelLeft className="size-4" />
            </Button>
          </div>
          <div className="mt-4 space-y-1">
            <button
              className="flex h-11 w-full items-center justify-start gap-2 rounded-full bg-sidebar-primary px-4 text-sm font-medium text-sidebar-primary-foreground transition hover:bg-[var(--signal-blue-hover)] disabled:cursor-not-allowed disabled:opacity-60"
              disabled={isCreating || isNewChatDisabled}
              onClick={onCreate}
              title={t.newConversation}
              type="button"
            >
              <MessageSquarePlus className="size-4" />
              {t.newChat}
            </button>
            <button
              className={cn(
                "flex h-10 w-full items-center justify-start gap-2 rounded-full px-4 text-sm font-medium transition hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
                activeView === "artifacts"
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                  : "text-sidebar-foreground"
              )}
              onClick={onOpenArtifacts}
              title={artifactsLabel}
              type="button"
            >
              <FileStack className="size-4" />
              {artifactsLabel}
            </button>
            <button
              className={cn(
                "flex h-10 w-full items-center justify-start gap-2 rounded-full px-4 text-sm font-medium transition hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
                activeView === "paper-bots"
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                  : "text-sidebar-foreground"
              )}
              onClick={onOpenPaperBots}
              title={paperBotsLabel}
              type="button"
            >
              <Bot className="size-4" />
              {paperBotsLabel}
            </button>
            <button
              className="flex h-10 w-full items-center justify-start gap-2 rounded-full px-4 text-sm font-medium text-sidebar-foreground transition hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              title={t.searchChats}
              type="button"
            >
              <Search className="size-4" />
              {t.searchChats}
            </button>
          </div>
          <div className="mt-6 min-h-0 flex-1 overflow-y-auto">
            <p className="px-3 pb-2 text-[11px] font-medium text-sidebar-foreground/55">{t.recents}</p>
            {isLoading ? (
              <SidebarSkeleton />
            ) : conversations.length === 0 ? (
              <div className="rounded-[8px] border border-dashed border-sidebar-border p-3 text-sm text-sidebar-foreground/65">
                {t.noConversations}
              </div>
            ) : (
              <div className="space-y-0.5">
                {conversations.map((item) => (
                  <div
                    className={cn(
                      "group flex items-center rounded-full text-sm text-sidebar-foreground/70 transition hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-within:bg-sidebar-accent",
                      selectedConversationId === item.conversation.id &&
                        "bg-sidebar-accent text-sidebar-accent-foreground"
                    )}
                    key={item.conversation.id}
                  >
                    <button
                      className="min-w-0 flex-1 px-3 py-2 text-left"
                      onClick={() => onSelect(item.conversation.id)}
                      type="button"
                    >
                      <span className="block truncate">
                        {item.conversation.title ??
                          item.last_message_preview ??
                          t.newChat}
                      </span>
                    </button>
                    <ConversationActionMenu
                      conversation={item.conversation}
                      onDelete={onDelete}
                      onRename={onRename}
                      triggerClassName={cn(
                        "mr-1 size-7 text-sidebar-foreground/55 opacity-0 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:opacity-100 group-hover:opacity-100",
                        selectedConversationId === item.conversation.id && "opacity-100"
                      )}
                    />
                  </div>
                ))}
              </div>
            )}
          </div>
          <SidebarAccountMenu
            accountUsage={accountUsage}
            language={language}
            me={me}
            onLanguageChange={onLanguageChange}
            onOpenDialog={onOpenAccountDialog}
            onOpenSettingsTab={onOpenSettingsTab}
            onThemeChange={onThemeChange}
            providerStatus={providerStatus}
            theme={theme}
          />
        </div>
      </div>
    </aside>
  );
}

function SidebarAccountMenu(props: {
  accountUsage?: AccountUsageResponse;
  language: UiLanguagePreference;
  me?: MeResponse;
  onLanguageChange: (language: UiLanguagePreference) => void;
  onOpenDialog: (dialog: AccountDialog) => void;
  onOpenSettingsTab: (tab: SettingsTab) => void;
  onThemeChange: (theme: ThemePreference) => void;
  providerStatus?: ProviderStatusResponse;
  theme: ThemePreference;
}) {
  return <ClerkSidebarAccountMenu {...props} />;
}

function ClerkSidebarAccountMenu(props: {
  accountUsage?: AccountUsageResponse;
  language: UiLanguagePreference;
  me?: MeResponse;
  onLanguageChange: (language: UiLanguagePreference) => void;
  onOpenDialog: (dialog: AccountDialog) => void;
  onOpenSettingsTab: (tab: SettingsTab) => void;
  onThemeChange: (theme: ThemePreference) => void;
  providerStatus?: ProviderStatusResponse;
  theme: ThemePreference;
}) {
  const { signOut } = useClerk();
  const { isLoaded, user } = useUser();
  const email = user?.primaryEmailAddress?.emailAddress ?? user?.emailAddresses[0]?.emailAddress ?? null;
  const displayName = accountName(props.me, user?.fullName ?? user?.username ?? null, email, props.language);
  const t = getUiCopy(props.language);
  return (
    <SidebarAccountMenuView
      {...props}
      avatarUrl={user?.imageUrl}
      displayName={isLoaded ? displayName : t.loadingAccount}
      onSignOut={() => signOut({ redirectUrl: "/sign-in" })}
      subtitle={isLoaded ? accountSubtitle(props.me, email, props.language) : t.checking}
    />
  );
}

function SidebarAccountMenuView({
  accountUsage,
  avatarUrl,
  displayName,
  language,
  me,
  onLanguageChange,
  onOpenDialog,
  onOpenSettingsTab,
  onSignOut,
  onThemeChange,
  subtitle,
  theme,
}: {
  accountUsage?: AccountUsageResponse;
  avatarUrl?: string;
  displayName: string;
  language: UiLanguagePreference;
  me?: MeResponse;
  onLanguageChange: (language: UiLanguagePreference) => void;
  onOpenDialog: (dialog: AccountDialog) => void;
  onOpenSettingsTab: (tab: SettingsTab) => void;
  onSignOut?: () => Promise<unknown>;
  onThemeChange: (theme: ThemePreference) => void;
  subtitle: string;
  theme: ThemePreference;
}) {
  const isFree = me?.capability.tier === "free";
  const t = getUiCopy(language);
  const themeLabel = themePreferenceLabel(theme, language);
  const usageLabel = accountUsage
    ? `${formatUsageNumber(accountUsage.total_tokens)} tokens this period`
    : me?.capability.tier_label ?? t.workspace;

  return (
    <div className="relative mt-3 border-t border-sidebar-border pt-3">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            className="flex w-full items-center gap-3 rounded-[6px] px-2 py-2 text-left transition hover:bg-sidebar-accent data-[state=open]:bg-sidebar-accent"
            type="button"
          >
            <span className="flex size-9 shrink-0 items-center justify-center overflow-hidden rounded-full bg-sidebar-primary text-sm font-medium text-sidebar-primary-foreground">
              {avatarUrl ? (
                <Image
                  alt=""
                  className="size-full object-cover"
                  height={36}
                  src={avatarUrl}
                  width={36}
                />
              ) : (
                accountInitial(displayName)
              )}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium text-sidebar-foreground">{displayName}</span>
              <span className="block truncate text-xs text-sidebar-foreground/55">{subtitle}</span>
            </span>
            <ChevronsUpDown className="size-4 shrink-0 text-sidebar-foreground/45" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="start"
          className="z-[60] w-[268px] overflow-visible rounded-[6px] border-sidebar-border bg-sidebar p-2 text-sidebar-foreground shadow-xl ring-0"
          side="top"
          sideOffset={8}
        >
          <div className="px-2 py-1.5">
            <span className="block truncate text-sm font-medium text-sidebar-foreground">{displayName}</span>
            <span className="block truncate text-xs font-normal text-sidebar-foreground/55">{usageLabel}</span>
          </div>
          <SidebarMenuSeparator />
          <SidebarAccountMenuItem
            onSelect={() => {
              onOpenDialog("settings");
            }}
          >
            <Settings className="size-4" />
            {t.settings}
          </SidebarAccountMenuItem>
          <SidebarPreferenceFlyout
            icon={<Globe2 className="size-4" />}
            label={t.language}
            valueLabel={languageLabel(language)}
          >
            <SidebarPreferenceOption
              active={isActivePreference(language, "en")}
              label="English"
              onSelect={() => onLanguageChange("en")}
            />
            <SidebarPreferenceOption
              active={isActivePreference(language, "vi")}
              label="Tiếng Việt"
              onSelect={() => onLanguageChange("vi")}
            />
          </SidebarPreferenceFlyout>
          <SidebarPreferenceFlyout
            icon={<MonitorCog className="size-4" />}
            label={t.appearance}
            valueLabel={themeLabel}
          >
            <SidebarPreferenceOption
              active={isActivePreference(theme, "system")}
              label={themePreferenceLabel("system", language)}
              onSelect={() => onThemeChange("system")}
            />
            <SidebarPreferenceOption
              active={isActivePreference(theme, "light")}
              label={themePreferenceLabel("light", language)}
              onSelect={() => onThemeChange("light")}
            />
            <SidebarPreferenceOption
              active={isActivePreference(theme, "dark")}
              label={themePreferenceLabel("dark", language)}
              onSelect={() => onThemeChange("dark")}
            />
          </SidebarPreferenceFlyout>
          <SidebarAccountMenuItem
            onSelect={() => {
              onOpenDialog("help");
            }}
          >
            <CircleHelp className="size-4" />
            {t.getHelp}
          </SidebarAccountMenuItem>
          {isFree && (
            <>
              <SidebarMenuSeparator />
              <SidebarAccountMenuItem
                className="text-[var(--together-accent-periwinkle)]"
                onSelect={() => {
                  onOpenSettingsTab("usage");
                }}
              >
                <CreditCard className="size-4" />
                {t.upgradePlan}
              </SidebarAccountMenuItem>
            </>
          )}
          <SidebarMenuSeparator />
          <SidebarAccountMenuItem
            disabled={!onSignOut}
            onSelect={() => {
              if (!onSignOut) {
                return;
              }
              void onSignOut();
            }}
          >
            <LogOut className="size-4" />
            {t.logOut}
            {!onSignOut && <span className="ml-auto text-xs text-sidebar-foreground/40">{t.local}</span>}
          </SidebarAccountMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

function SidebarMenuSeparator() {
  return <div className="-mx-1 my-1 h-px bg-sidebar-border" />;
}

function SidebarAccountMenuItem({
  children,
  className,
  disabled,
  onSelect,
}: {
  children: ReactNode;
  className?: string;
  disabled?: boolean;
  onSelect: () => void;
}) {
  return (
    <DropdownMenuItem
      className={cn(
        "flex cursor-default items-center gap-2 rounded-md px-1.5 py-1 text-sm text-sidebar-foreground outline-none transition focus:bg-sidebar-accent focus:text-sidebar-accent-foreground data-disabled:pointer-events-none data-disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0",
        className
      )}
      disabled={disabled}
      onSelect={onSelect}
    >
      {children}
    </DropdownMenuItem>
  );
}

function SidebarPreferenceFlyout({
  children,
  icon,
  label,
  valueLabel,
}: {
  children: ReactNode;
  icon: ReactNode;
  label: string;
  valueLabel: string;
}) {
  return (
    <DropdownMenuSub>
      <DropdownMenuSubTrigger className="rounded-md px-1.5 py-1 text-sidebar-foreground focus:bg-sidebar-accent focus:text-sidebar-accent-foreground data-open:bg-sidebar-accent data-open:text-sidebar-accent-foreground">
        {icon}
        <span>{label}</span>
        <span className="ml-auto truncate text-xs text-sidebar-foreground/45">{valueLabel}</span>
      </DropdownMenuSubTrigger>
      <DropdownMenuSubContent
        className="z-[70] w-44 rounded-[6px] border-sidebar-border bg-sidebar p-1 text-sidebar-foreground shadow-xl ring-0"
        sideOffset={8}
      >
        {children}
      </DropdownMenuSubContent>
    </DropdownMenuSub>
  );
}

function SidebarPreferenceOption({
  active,
  label,
  onSelect,
}: {
  active: boolean;
  label: string;
  onSelect: () => void;
}) {
  return (
    <DropdownMenuItem
      className="flex cursor-default items-center gap-2 rounded-[4px] px-2 py-1.5 text-sm text-sidebar-foreground outline-none transition focus:bg-sidebar-accent focus:text-sidebar-accent-foreground"
      onSelect={onSelect}
    >
      <span className="flex size-4 items-center justify-center">
        {active && <Check className="size-3.5" />}
      </span>
      <span>{label}</span>
    </DropdownMenuItem>
  );
}

function isActivePreference<T extends string>(current: T, expected: T) {
  return current === expected;
}

export function conversationHydrationKey(conversationId: string, messages: StrategyChatMessage[]) {
  return [
    conversationId,
    ...messages.map(
      (message) => `${message.id}:${message.role}:${getMessageText(message)}`
    ),
  ].join("|");
}

function ConversationActionMenu({
  conversation,
  onDelete,
  onRename,
  triggerClassName,
}: {
  conversation: ChatConversation;
  onDelete: (conversation: ChatConversation) => void;
  onRename: (conversation: ChatConversation) => void;
  triggerClassName?: string;
}) {
  const { t } = useI18n();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          aria-label={`${t.openActionsFor} ${conversation.title ?? t.newChat}`}
          className={cn("rounded-[4px]", triggerClassName)}
          onClick={(event) => event.stopPropagation()}
          size="icon-sm"
          type="button"
          variant="ghost"
        >
          <MoreHorizontal className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-44">
        <DropdownMenuItem onSelect={() => onRename(conversation)}>
          <Pencil className="size-4" />
          {t.rename}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={() => onDelete(conversation)}
          variant="destructive"
        >
          <Trash2 className="size-4" />
          {t.delete}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function AccountDialogs({
  accountUsage,
  dialog,
  healthChecking,
  language,
  lastHealthCheckedAt,
  me,
  onCopyValue,
  onHealthCheck,
  onLanguageChange,
  onOpenChange,
  onRetryUsage,
  onSettingsTabChange,
  onThemeChange,
  providerStatus,
  readiness,
  resolvedTheme,
  settingsTab,
  theme,
  usageError,
  usageLoading,
}: {
  accountUsage?: AccountUsageResponse;
  dialog: AccountDialog | null;
  healthChecking: boolean;
  language: UiLanguagePreference;
  lastHealthCheckedAt: number;
  me?: MeResponse;
  onCopyValue: (value: string, successTitle: string) => void;
  onHealthCheck: () => void;
  onLanguageChange: (language: UiLanguagePreference) => void;
  onOpenChange: (open: boolean) => void;
  onRetryUsage: () => void;
  onSettingsTabChange: (tab: SettingsTab) => void;
  onThemeChange: (theme: ThemePreference) => void;
  providerStatus?: ProviderStatusResponse;
  readiness?: ReadyResponse;
  resolvedTheme: ResolvedTheme;
  settingsTab: SettingsTab;
  theme: ThemePreference;
  usageError: boolean;
  usageLoading: boolean;
}) {
  const provider = providerDisplay(providerStatus, me, language);
  const t = getUiCopy(language);
  const title = accountDialogTitle(dialog, language);
  const workspaceId = me?.workspace.id ?? "local-workspace";
  const userId = me?.user.id ?? t.localWorkspace;
  const providerUnavailable = providerStatus ? !providerStatus.available : false;
  const llmReadinessStatus = readiness?.checks.llm_provider?.status ?? readiness?.status;
  const modelRouteReady = providerRouteReady(providerStatus);
  const fallbackEnabled = providerFallbackEnabled(providerStatus);

  if (dialog === "settings") {
    const tabs: Array<{ icon: ReactNode; label: string; value: SettingsTab }> = [
      { icon: <Settings className="size-4" />, label: t.general, value: "general" },
      { icon: <Gauge className="size-4" />, label: t.aiProvider, value: "provider" },
      { icon: <CreditCard className="size-4" />, label: t.usageAndPlan, value: "usage" },
      { icon: <Building2 className="size-4" />, label: t.workspace, value: "workspace" },
    ];
    const activeTitle = tabs.find((tab) => tab.value === settingsTab)?.label ?? t.settings;

    return (
      <Dialog onOpenChange={onOpenChange} open>
        <DialogContent
          className="overflow-hidden p-0 sm:max-w-3xl"
          onFocusOutside={preventDialogOutsideInteraction}
          onInteractOutside={preventDialogOutsideInteraction}
          onPointerDownOutside={preventDialogOutsideInteraction}
        >
          <div className="grid min-h-[460px] grid-cols-1 md:grid-cols-[190px_1fr]">
            <nav className="border-border border-b bg-muted/30 p-3 md:border-r md:border-b-0">
              <div className="flex gap-1 md:grid">
                {tabs.map((tab) => (
                  <button
                    className={cn(
                      "flex min-w-0 items-center gap-2 rounded-[6px] px-3 py-2 text-left text-sm transition",
                      settingsTab === tab.value
                        ? "bg-background text-foreground shadow-sm"
                        : "text-muted-foreground hover:bg-background/70 hover:text-foreground"
                    )}
                    key={tab.value}
                    onClick={() => onSettingsTabChange(tab.value)}
                    type="button"
                  >
                    {tab.icon}
                    <span className="truncate">{tab.label}</span>
                  </button>
                ))}
              </div>
            </nav>
            <section className="min-w-0 p-5">
              <DialogHeader>
                <DialogTitle>{activeTitle}</DialogTitle>
              </DialogHeader>
              <div className="mt-5">
                {settingsTab === "general" && (
                  <div className="space-y-3">
                    <AccountInfoRow label={t.accountLabel} value={me?.user.id ?? t.localWorkspace} />
                    <AccountInfoRow label={t.plan} value={me?.capability.tier_label ?? t.workspace} />
                    <SettingsSelectRow
                      label={t.theme}
                      onValueChange={(value) => onThemeChange(value as ThemePreference)}
                      value={theme}
                    >
                      <SelectItem value="system">{themePreferenceLabel("system", language)}</SelectItem>
                      <SelectItem value="light">{themePreferenceLabel("light", language)}</SelectItem>
                      <SelectItem value="dark">{themePreferenceLabel("dark", language)}</SelectItem>
                    </SettingsSelectRow>
                    <SettingsSelectRow
                      label={t.language}
                      onValueChange={(value) => onLanguageChange(value as UiLanguagePreference)}
                      value={language}
                    >
                      <SelectItem value="en">English</SelectItem>
                      <SelectItem value="vi">Tiếng Việt</SelectItem>
                    </SettingsSelectRow>
                  </div>
                )}
                {settingsTab === "provider" && (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-muted-foreground text-sm">{t.providerStatus}</span>
                      <Button
                        disabled={healthChecking}
                        onClick={onHealthCheck}
                        size="sm"
                        type="button"
                        variant="outline"
                      >
                        <RefreshCcw className={cn("size-3.5", healthChecking && "animate-spin")} />
                        {t.runHealthCheck}
                      </Button>
                    </div>
                    {providerUnavailable && (
                      <div className="flex items-center justify-between gap-3 rounded-[6px] border border-destructive/30 bg-destructive/5 px-3 py-2">
                        <span className="truncate text-sm text-destructive">
                          {providerStatus?.reason ?? t.providerUnavailable}
                        </span>
                      </div>
                    )}
                    <AccountInfoRow label={t.providerStatus} value={provider.title} />
                    <AccountInfoRow
                      label={t.modelRoute}
                      value={
                        modelRouteReady
                          ? providerStatus?.user_message ?? t.routeReady
                          : providerStatus?.user_message ?? t.routeUnavailable
                      }
                    />
                    <AccountInfoRow
                      label={t.modelRoutingMode}
                      value={providerStatus?.model_routing_mode ?? "-"}
                    />
                    <AccountInfoRow label={t.readinessStatus} value={statusLabel(llmReadinessStatus, language)} />
                    <AccountInfoRow label={t.lastChecked} value={formatLastChecked(lastHealthCheckedAt, language)} />
                    <AccountInfoRow label={t.currentPlan} value={providerStatus?.tier_label ?? me?.capability.tier_label ?? t.workspace} />
                    <AccountInfoRow
                      label={t.fallbackEnabled}
                      value={fallbackEnabled ? t.fallbackEnabled : t.fallbackDisabled}
                    />
                    <AccountInfoRow
                      label={t.aiProvider}
                      value={formatModeList(providerStatus?.available_gateways)}
                    />
                    <AccountInfoRow
                      label={t.modelStageDefaults}
                      value={formatStageDefaults(providerStatus?.selected_stage_defaults)}
                    />
                    <AccountInfoRow label={t.runModes} value={formatModeList(providerStatus?.allowed_run_modes)} />
                  </div>
                )}
                {settingsTab === "usage" && (
                  <UsageDialogContent
                    accountUsage={accountUsage}
                    loading={usageLoading}
                    onRetry={onRetryUsage}
                    showUpgrade={false}
                    language={language}
                    usageError={usageError}
                  />
                )}
                {settingsTab === "workspace" && (
                  <div className="space-y-3">
                    <CopyInfoRow
                      label={t.workspace}
                      onCopy={() => onCopyValue(workspaceId, t.copiedWorkspaceId)}
                      value={workspaceId}
                      copyLabel={t.copy}
                    />
                    <CopyInfoRow
                      label={t.userId}
                      onCopy={() => onCopyValue(userId, t.copiedUserId)}
                      value={userId}
                      copyLabel={t.copy}
                    />
                    <AccountInfoRow label={t.role} value={me?.workspace.role ?? "owner"} />
                    <AccountInfoRow label={t.plan} value={me?.capability.tier_label ?? t.workspace} />
                  </div>
                )}
              </div>
            </section>
          </div>
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <Dialog onOpenChange={onOpenChange} open={dialog !== null}>
      <DialogContent
        className="sm:max-w-lg"
        onFocusOutside={preventDialogOutsideInteraction}
        onInteractOutside={preventDialogOutsideInteraction}
        onPointerDownOutside={preventDialogOutsideInteraction}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        {dialog === "language" && (
          <div className="space-y-3">
            <Select value={language} onValueChange={(value) => onLanguageChange(value as UiLanguagePreference)}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="en">English</SelectItem>
                <SelectItem value="vi">Tiếng Việt</SelectItem>
              </SelectContent>
            </Select>
          </div>
        )}
        {dialog === "appearance" && (
          <div className="space-y-3">
            <Select value={theme} onValueChange={(value) => onThemeChange(value as ThemePreference)}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="system">System</SelectItem>
                <SelectItem value="light">Light</SelectItem>
                <SelectItem value="dark">Dark</SelectItem>
              </SelectContent>
            </Select>
            <AccountInfoRow label={t.currentlyUsing} value={resolvedThemeLabel(resolvedTheme, language)} />
          </div>
        )}
        {dialog === "help" && (
          <div className="space-y-3">
            <AccountInfoRow label={t.docs} value={t.comingSoon} />
            <AccountInfoRow label={t.support} value={t.comingSoon} />
            <AccountInfoRow label={t.reportIssue} value={t.comingSoon} />
            <p className="text-muted-foreground text-sm">
              {t.reviewOnlyBoundary}
            </p>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function UsageDialogContent({
  accountUsage,
  language,
  loading,
  onRetry,
  showUpgrade,
  usageError,
}: {
  accountUsage?: AccountUsageResponse;
  language: UiLanguagePreference;
  loading: boolean;
  onRetry: () => void;
  showUpgrade: boolean;
  usageError: boolean;
}) {
  const t = getUiCopy(language);
  if (loading) {
    return <p className="text-muted-foreground text-sm">{t.loadingUsage}</p>;
  }
  if (usageError) {
    return (
      <div className="space-y-3">
        <p className="text-muted-foreground text-sm">{t.usageUnavailable}</p>
        <Button onClick={onRetry} size="sm" type="button" variant="outline">
          {t.tryAgain}
        </Button>
      </div>
    );
  }
  if (!accountUsage) {
    return <p className="text-muted-foreground text-sm">{t.noUsage}</p>;
  }
  return (
    <div className="space-y-4">
      <div className="rounded-[6px] border border-border p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="font-medium text-sm">{accountUsage.tier_label}</p>
            <p className="text-muted-foreground text-xs">
              {formatUsagePeriod(accountUsage)}
            </p>
          </div>
              {showUpgrade && (
            <Button size="sm" type="button" variant="outline">
              {t.upgradePlan}
            </Button>
          )}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <UsageStat label={t.messages} value={accountUsage.messages} />
        <UsageStat label={t.runs} value={accountUsage.runs} />
        <UsageStat label={t.artifacts} value={accountUsage.artifacts} />
        <UsageStat label={t.tokens} value={accountUsage.total_tokens} />
      </div>
      <AccountInfoRow label={t.estimatedCost} value={formatUsageCost(accountUsage, language)} />
    </div>
  );
}

function UsageStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-[6px] border border-border p-3">
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className="mt-1 font-medium text-sm">{formatUsageNumber(value)}</p>
    </div>
  );
}

function AccountInfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-[6px] border border-border px-3 py-2">
      <span className="text-muted-foreground text-sm">{label}</span>
      <span className="truncate text-sm">{value}</span>
    </div>
  );
}

function preventDialogOutsideInteraction(event: Event) {
  event.preventDefault();
}

function CopyInfoRow({
  copyLabel,
  label,
  onCopy,
  value,
}: {
  copyLabel: string;
  label: string;
  onCopy: () => void;
  value: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-[6px] border border-border px-3 py-2">
      <span className="text-muted-foreground text-sm">{label}</span>
      <div className="flex min-w-0 items-center gap-2">
        <span className="truncate text-sm">{value}</span>
        <Button
          aria-label={`${copyLabel} ${label}`}
          className="shrink-0"
          onClick={onCopy}
          size="icon-xs"
          type="button"
          variant="ghost"
        >
          <Clipboard className="size-3.5" />
        </Button>
      </div>
    </div>
  );
}

function SettingsSelectRow({
  children,
  label,
  onValueChange,
  value,
}: {
  children: ReactNode;
  label: string;
  onValueChange: (value: string) => void;
  value: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-[6px] border border-border px-3 py-2">
      <span className="text-muted-foreground text-sm">{label}</span>
      <Select onValueChange={onValueChange} value={value}>
        <SelectTrigger className="h-8 w-40">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>{children}</SelectContent>
      </Select>
    </div>
  );
}

function formatModeList(modes?: string[]) {
  return modes?.length ? modes.join(", ") : "-";
}

function formatStageDefaults(defaults?: Record<string, string>) {
  if (!defaults || Object.keys(defaults).length === 0) {
    return "-";
  }
  return Object.entries(defaults)
    .map(([stage, route]) => `${stage}: ${route}`)
    .join(", ");
}

function accountDialogTitle(dialog: AccountDialog | null, language: UiLanguagePreference = "en") {
  const t = getUiCopy(language);
  switch (dialog) {
    case "settings":
      return t.settings;
    case "language":
      return t.language;
    case "appearance":
      return t.appearance;
    case "help":
      return t.getHelp;
    default:
      return t.accountLabel;
  }
}

function statusLabel(status: string | undefined, language: UiLanguagePreference = "en") {
  const t = getUiCopy(language);
  if (!status) {
    return "-";
  }
  const normalized = status.toLowerCase();
  if (normalized === "ok" || normalized === "ready" || normalized === "pass") {
    return t.readyStatus;
  }
  if (normalized === "unavailable" || normalized === "failed" || normalized === "fail") {
    return t.needsSetupStatus;
  }
  return status;
}

function formatLastChecked(timestamp: number, language: UiLanguagePreference = "en") {
  if (!timestamp) {
    return "-";
  }
  return new Intl.DateTimeFormat(languageLocale(language), {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(timestamp));
}

function themePreferenceLabel(theme: ThemePreference, language: UiLanguagePreference = "en") {
  const t = getUiCopy(language);
  switch (theme) {
    case "dark":
      return t.themeDark;
    case "light":
      return t.themeLight;
    case "system":
      return t.themeSystem;
  }
}

function resolvedThemeLabel(theme: ResolvedTheme, language: UiLanguagePreference = "en") {
  const t = getUiCopy(language);
  return theme === "dark" ? t.dark : t.light;
}

function formatUsagePeriod(usage: AccountUsageResponse) {
  const formatter = new Intl.DateTimeFormat("en-US", {
    day: "numeric",
    month: "short",
  });
  return `${formatter.format(new Date(usage.period_start))} - ${formatter.format(new Date(usage.period_end))}`;
}

export function RenameConversationDialog({
  disabled,
  language,
  onOpenChange,
  onRename,
  open,
  setTitle,
  title,
}: {
  disabled: boolean;
  language: UiLanguagePreference;
  onOpenChange: (open: boolean) => void;
  onRename: () => void;
  open: boolean;
  setTitle: (title: string) => void;
  title: string;
}) {
  const t = getUiCopy(language);
  const canSubmit = title.trim().length > 0 && !disabled;
  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>{t.renameChat}</DialogTitle>
          <DialogDescription>
            {t.renameDescription}
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (canSubmit) {
              onRename();
            }
          }}
        >
          <Input
            autoFocus
            maxLength={160}
            onChange={(event) => setTitle(event.target.value)}
            value={title}
          />
          <DialogFooter>
            <Button
              disabled={disabled}
              onClick={() => onOpenChange(false)}
              type="button"
              variant="outline"
            >
              {t.cancel}
            </Button>
            <Button disabled={!canSubmit} type="submit">
              {t.rename}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function DeleteConversationDialog({
  conversationTitle,
  disabled,
  language,
  onDelete,
  onOpenChange,
  open,
}: {
  conversationTitle: string;
  disabled: boolean;
  language: UiLanguagePreference;
  onDelete: () => void;
  onOpenChange: (open: boolean) => void;
  open: boolean;
}) {
  const t = getUiCopy(language);
  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>{t.deleteThisChat}</DialogTitle>
          <DialogDescription>
            {t.deleteChatDescription.replace("{title}", conversationTitle)}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            disabled={disabled}
            onClick={() => onOpenChange(false)}
            type="button"
            variant="outline"
          >
            {t.cancel}
          </Button>
          <Button
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            disabled={disabled}
            onClick={onDelete}
            type="button"
          >
            {t.delete}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SidebarRailButton({
  active,
  disabled,
  icon,
  label,
  onClick,
}: {
  active?: boolean;
  disabled?: boolean;
  icon: ReactNode;
  label: string;
  onClick?: () => void;
}) {
  return (
    <button
      aria-label={label}
      className={cn(
        "flex size-9 items-center justify-center rounded-[8px] transition hover:bg-sidebar-accent hover:text-sidebar-accent-foreground disabled:cursor-not-allowed disabled:opacity-40",
        active
          ? "bg-sidebar-accent text-sidebar-accent-foreground"
          : "text-sidebar-foreground/85"
      )}
      disabled={disabled}
      onClick={onClick}
      title={label}
      type="button"
    >
      {icon}
    </button>
  );
}

function activeConversationLabel(
  conversations: ConversationSidebarItem[],
  selectedConversationId: string | null,
  t: ReturnType<typeof getUiCopy>
) {
  if (!selectedConversationId) {
    return t.selectConversation;
  }
  const item = conversations.find(
    (conversation) => conversation.conversation.id === selectedConversationId
  );
  return item?.conversation.title ?? item?.last_message_preview ?? t.selectConversation;
}

function StrategyLogoMark({ compact = false }: { compact?: boolean }) {
  return (
    <div
      aria-label="Strategy Codebot"
      className={cn(
        "flex items-center justify-center overflow-hidden bg-white",
        compact ? "size-5 rounded-full" : "size-8 rounded-full"
      )}
      title="Strategy Codebot"
    >
      <Image
        alt=""
        className="size-full object-cover"
        height={compact ? 20 : 32}
        src="/brand/strategy-codebot-icon-192.png"
        width={compact ? 20 : 32}
      />
    </div>
  );
}

export function ReadinessStrip({
  conversation,
  onDelete,
  onRename,
  title,
}: {
  conversation: ChatConversation | null;
  onDelete: (conversation: ChatConversation) => void;
  onRename: (conversation: ChatConversation) => void;
  title: string;
}) {
  return (
    <header className="relative flex min-h-16 items-center justify-between gap-3 border-b border-sidebar-border bg-sidebar px-4 text-sidebar-foreground">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <Bot className="size-4 text-sidebar-foreground/64" />
          <p className="truncate font-medium text-sm">{title}</p>
          {conversation ? (
            <ConversationActionMenu
              conversation={conversation}
              onDelete={onDelete}
              onRename={onRename}
              triggerClassName="size-7 text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
            />
          ) : null}
        </div>
      </div>
    </header>
  );
}

function SidebarSkeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 5 }).map((_, index) => (
        <div className="h-16 rounded-[4px] bg-muted" key={index} />
      ))}
    </div>
  );
}
