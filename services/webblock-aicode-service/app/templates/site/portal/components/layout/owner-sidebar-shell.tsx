"use client";

import { useState, useTransition } from "react";
import { ArrowLeft, Logout, SidebarLeft } from "iconsax-reactjs";
import { signOutAction } from "@/portal/features/auth/actions";
import { Avatar } from "@/portal/components/common/avatar";
import { ConfirmDialog } from "@/portal/components/common/confirm-dialog";
import { ownerNav } from "@/portal/config/nav";
import type { AppLinks } from "@/portal/config/site";
import { initials } from "@/portal/lib/format";
import { cn } from "@/portal/lib/utils";
import type { Profile } from "@/portal/features/account";
import type { Website } from "@/portal/features/websites";
import { BrandLogo } from "./brand-logo";
import { SidebarNav } from "./sidebar-nav";

/** Interactive part of the Owner Portal sidebar: collapse toggle and the log out confirmation. */
export function OwnerSidebarShell({
  website,
  profile,
  links,
}: {
  website: Pick<Website, "id" | "name" | "domain">;
  profile: Pick<Profile, "fullName" | "initials" | "avatarUrl">;
  links: AppLinks;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [signingOut, startSignOut] = useTransition();

  const shopMark = (
    <span
      aria-hidden
      className="flex size-9 shrink-0 items-center justify-center rounded-[10px] bg-brand-soft text-[13px] leading-[18px] font-bold text-brand-strong"
    >
      {initials(website.name)}
    </span>
  );
  const shopText = (
    <span className="min-w-0 flex-1">
      <span className="block truncate text-sm leading-5 font-semibold text-foreground">{website.name}</span>
      <span className="block truncate text-[11px] leading-4 text-muted-foreground">
        {website.domain ?? "Not published yet"}
      </span>
    </span>
  );
  const shopClasses = cn(
    "flex items-center gap-3 rounded-xl border border-border bg-page p-2.5",
    collapsed && "justify-center border-0 bg-transparent p-0",
  );

  return (
    <aside
      className={cn(
        "sticky top-0 flex h-dvh shrink-0 flex-col border-r border-border bg-background px-4 pt-5 pb-4 transition-[width] duration-200",
        collapsed ? "w-[84px]" : "w-[260px]",
      )}
    >
      <div className={cn("flex items-center", collapsed ? "flex-col gap-3" : "justify-between pl-1")}>
        <BrandLogo href={links.home} compact={collapsed} />
        <button
          type="button"
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!collapsed}
          onClick={() => setCollapsed((value) => !value)}
          className="flex size-8 shrink-0 items-center justify-center rounded-full border border-border bg-background text-foreground-soft outline-none transition-colors hover:bg-primary-soft focus-visible:ring-3 focus-visible:ring-ring/40"
        >
          <SidebarLeft aria-hidden className="size-4" />
        </button>
      </div>

      <div className="pt-5 pb-5">
        {website.domain ? (
          <a
            href={`https://${website.domain}`}
            target="_blank"
            rel="noopener noreferrer"
            title={collapsed ? website.name : undefined}
            className={cn(shopClasses, "outline-none transition-colors hover:border-primary/40 focus-visible:ring-3 focus-visible:ring-ring/40")}
          >
            {shopMark}
            {collapsed ? <span className="sr-only">{website.name}</span> : shopText}
          </a>
        ) : (
          <div className={shopClasses}>
            {shopMark}
            {collapsed ? <span className="sr-only">{website.name}</span> : shopText}
          </div>
        )}
      </div>

      <SidebarNav items={ownerNav()} collapsed={collapsed} />

      <div className="mt-auto flex flex-col gap-0.5">
        {links.editor && (
        <a
          href={links.editor}
          title={collapsed ? "Back to editor" : undefined}
          className={cn(
            "flex h-10 items-center gap-3 rounded-full px-3 text-sm font-medium text-foreground-soft outline-none transition-colors hover:bg-surface hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/40",
            collapsed && "justify-center px-0",
          )}
        >
          <ArrowLeft aria-hidden className="size-[18px] shrink-0" />
          <span className={cn(collapsed && "sr-only")}>Back to editor</span>
        </a>
        )}
        <div className="py-2.5">
          <div className="h-px bg-border" />
        </div>
        <div className={cn("flex items-center gap-2.5 pr-1 pl-2", collapsed && "flex-col p-0")}>
          <Avatar
            name={profile.fullName}
            initials={profile.initials}
            src={profile.avatarUrl}
            className="size-[34px] text-xs"
          />
          {!collapsed && (
            <div className="min-w-0 flex-1">
              <p className="truncate text-[13px] leading-[19.5px] font-semibold text-foreground">{profile.fullName}</p>
              <p className="text-xs leading-[18px] text-muted-foreground">Owner</p>
            </div>
          )}
          <button
            type="button"
            aria-label="Log out"
            onClick={() => setConfirmOpen(true)}
            className="flex size-8 shrink-0 items-center justify-center rounded-lg text-foreground-soft outline-none transition-colors hover:bg-primary-soft hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/40"
          >
            <Logout aria-hidden className="size-[18px]" />
          </button>
        </div>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        onConfirm={() => startSignOut(() => signOutAction())}
        loading={signingOut}
        icon={<Logout />}
        title="Log out of WebBlock?"
        description="You’ll need to sign in again to manage your website."
        confirmLabel="Log out"
        look="soft"
      />
    </aside>
  );
}
