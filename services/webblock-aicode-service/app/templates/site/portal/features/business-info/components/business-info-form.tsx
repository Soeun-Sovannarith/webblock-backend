"use client";

import { useActionState, useEffect, useRef, useState, useTransition, type ReactNode } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Camera, Link as LinkIcon, Location, Lock, Mobile, Sms } from "iconsax-reactjs";
import { updateBusinessInfoAction } from "../actions";
import { toast } from "@/portal/components/common/toast";
import { Button } from "@/portal/components/ui/button";
import { Input } from "@/portal/components/ui/input";
import { SelectField } from "@/portal/components/common/select-field";
import { Switch } from "@/portal/components/ui/switch";
import { Textarea } from "@/portal/components/ui/textarea";
import { formatClock } from "@/portal/lib/format";
import { checkImage, LOGO_RULE } from "@/portal/lib/image-rules";
import { cn } from "@/portal/lib/utils";
import { TIME_OPTIONS } from "@/portal/features/websites/schemas";
import type { OpeningHour } from "@/portal/features/websites/types";
import type { BusinessInfoView } from "../service";
import type { BusinessInfo } from "../types";
import {
  BusinessIdentity,
  BusinessMark,
  DAY_LABEL,
  InfoCard,
  InfoCardTitle,
  SectionLabel,
  SiteBanner,
} from "./business-info-parts";

const fieldClasses = "h-[42px] rounded-xl text-sm";

function parseHours(raw: string | undefined, fallback: OpeningHour[]): OpeningHour[] {
  if (!raw) return fallback;
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) && parsed.length === 7 ? (parsed as OpeningHour[]) : fallback;
  } catch {
    return fallback;
  }
}

/** Edit mode of Business information: the same layout as the details, with fields in place. */
export function BusinessInfoForm({ info, website }: { info: BusinessInfo; website: BusinessInfoView["website"] }) {
  const router = useRouter();
  const pathname = usePathname();
  const [state, formAction, saving] = useActionState(updateBusinessInfoAction.bind(null, website.id), null);
  const [, startTransition] = useTransition();
  const values = state && !state.ok ? state.values : undefined;
  const errors = state && !state.ok ? state.fieldErrors : undefined;
  const error = (name: string) => errors?.[name]?.[0];

  const [hours, setHours] = useState<OpeningHour[]>(() => parseHours(values?.hours, info.hours));
  const logoInput = useRef<HTMLInputElement>(null);
  const [logoPreview, setLogoPreview] = useState<string | null>(info.logoUrl);
  const [logoError, setLogoError] = useState<string | null>(null);

  const handled = useRef(state);
  useEffect(() => {
    if (!state || handled.current === state) return;
    handled.current = state;
    if (state.ok) {
      toast.success("Business information saved", state.message);
      router.replace(pathname, { scroll: false });
    } else if (!state.fieldErrors) {
      toast.error("Could not save your details", state.message);
    }
  }, [state, router, pathname]);

  useEffect(() => {
    if (!logoPreview?.startsWith("blob:")) return;
    return () => URL.revokeObjectURL(logoPreview);
  }, [logoPreview]);

  function chooseLogo(file: File | undefined) {
    if (!file) return;
    const problem = checkImage(file, LOGO_RULE);
    setLogoError(problem);
    if (problem) {
      if (logoInput.current) logoInput.current.value = "";
      return;
    }
    setLogoPreview(URL.createObjectURL(file));
  }

  function updateHour(day: OpeningHour["day"], change: Partial<OpeningHour>) {
    setHours((current) => current.map((h) => (h.day === day ? { ...h, ...change } : h)));
  }

  const hoursError = error("hours");
  const photoError = logoError ?? error("logo");

  return (
    <form
      noValidate
      className="flex flex-col gap-4"
      // Submit without React's automatic form reset so typed values and the picked logo survive an error.
      onSubmit={(event) => {
        event.preventDefault();
        const formData = new FormData(event.currentTarget);
        startTransition(() => formAction(formData));
      }}
    >
      <input type="hidden" name="hours" value={JSON.stringify(hours)} />

      <InfoCard className="p-2.5">
        <SiteBanner website={website} />
        <div className="flex flex-wrap items-center gap-4 px-4 pt-4 pb-3">
          <div className="relative">
            <BusinessMark name={website.name} logoUrl={logoPreview} />
            <button
              type="button"
              aria-label="Change logo"
              aria-describedby={photoError ? "logo-error" : undefined}
              onClick={() => logoInput.current?.click()}
              className="absolute -right-1 -bottom-1 flex size-6 items-center justify-center rounded-full border-2 border-background bg-brand-soft text-brand-strong outline-none transition-colors hover:bg-primary-soft focus-visible:ring-3 focus-visible:ring-ring/40"
            >
              <Camera aria-hidden className="size-3" />
            </button>
            <input
              ref={logoInput}
              type="file"
              name="logo"
              accept={LOGO_RULE.types.join(",")}
              className="sr-only"
              tabIndex={-1}
              aria-label="Logo file"
              onChange={(event) => chooseLogo(event.target.files?.[0])}
            />
          </div>
          <div className="flex-1">
            <BusinessIdentity website={website}>
              <p className="flex items-center gap-1.5 pt-1 text-xs text-muted-foreground">
                <Lock aria-hidden className="size-3.5" />
                Name, business type and web address can’t be changed
              </p>
              {photoError && (
                <p id="logo-error" role="alert" className="pt-1 text-xs text-danger">
                  {photoError}
                </p>
              )}
            </BusinessIdentity>
          </div>
          <div className="flex items-center gap-2">
            <Button asChild variant="secondary" aria-disabled={saving}>
              <Link href={pathname} scroll={false}>
                Cancel
              </Link>
            </Button>
            <Button type="submit" loading={saving}>
              Save changes
            </Button>
          </div>
        </div>
      </InfoCard>

      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)]">
        <InfoCard>
          <InfoCardTitle>Business details</InfoCardTitle>
          <SectionLabel>About</SectionLabel>
          <Field id="info-description" label="Business description" error={error("description")}>
            <Textarea
              id="info-description"
              name="description"
              rows={3}
              defaultValue={values?.description ?? info.description}
              aria-invalid={!!error("description")}
              aria-describedby={error("description") ? "info-description-error" : undefined}
              className="min-h-[78px] resize-none rounded-xl px-3 py-2.5 text-sm"
            />
          </Field>

          <SectionLabel>Contact</SectionLabel>
          <div className="flex flex-col gap-3">
            <Field id="info-phone" label="Phone number" error={error("phone")}>
              <Input
                id="info-phone"
                name="phone"
                type="tel"
                icon={<Mobile />}
                iconClassName="left-3"
                defaultValue={values?.phone ?? info.phone}
                aria-invalid={!!error("phone")}
                aria-describedby={error("phone") ? "info-phone-error" : undefined}
                className={cn(fieldClasses, "pl-10")}
              />
            </Field>
            <Field id="info-email" label="Email" error={error("email")}>
              <Input
                id="info-email"
                name="email"
                type="email"
                icon={<Sms />}
                iconClassName="left-3"
                defaultValue={values?.email ?? info.email}
                aria-invalid={!!error("email")}
                aria-describedby={error("email") ? "info-email-error" : undefined}
                className={cn(fieldClasses, "pl-10")}
              />
            </Field>
          </div>

          <SectionLabel>Location</SectionLabel>
          <div className="flex flex-col gap-3">
            <Field id="info-address" label="Address" error={error("address")}>
              <Input
                id="info-address"
                name="address"
                icon={<Location />}
                iconClassName="left-3"
                defaultValue={values?.address ?? info.address}
                aria-invalid={!!error("address")}
                aria-describedby={error("address") ? "info-address-error" : undefined}
                className={cn(fieldClasses, "pl-10")}
              />
            </Field>
            <Field id="info-maps" label="Google Maps link" optional error={error("mapsUrl")}>
              <Input
                id="info-maps"
                name="mapsUrl"
                type="url"
                inputMode="url"
                placeholder="https://maps.app.goo.gl/…"
                icon={<LinkIcon />}
                iconClassName="left-3"
                defaultValue={values?.mapsUrl ?? info.mapsUrl ?? ""}
                aria-invalid={!!error("mapsUrl")}
                aria-describedby={error("mapsUrl") ? "info-maps-error" : undefined}
                className={cn(fieldClasses, "pl-10")}
              />
            </Field>
          </div>
        </InfoCard>

        <InfoCard>
          <InfoCardTitle hint="Turn on the days you open, then set the opening and closing time.">Opening hours</InfoCardTitle>
          <fieldset aria-describedby={hoursError ? "info-hours-error" : undefined}>
            <legend className="sr-only">Opening hours</legend>
            <div
              aria-hidden
              className="grid grid-cols-[minmax(0,1fr)_120px_120px] gap-x-2 border-b border-border pb-2 text-xs text-muted-foreground sm:grid-cols-[minmax(0,1fr)_110px_110px]"
            >
              <span>Day</span>
              <span>Opens</span>
              <span>Closes</span>
            </div>
            {hours.map((hour) => {
              const label = DAY_LABEL[hour.day];
              const invalid = hour.open && hour.opensAt >= hour.closesAt;
              return (
                <div
                  key={hour.day}
                  className="grid min-h-[56px] grid-cols-[minmax(0,1fr)_120px_120px] items-center gap-x-2 border-b border-border py-2 last:border-0 sm:grid-cols-[minmax(0,1fr)_110px_110px]"
                >
                  <div className="flex items-center gap-3">
                    <span className="w-[88px] text-sm text-foreground">{label}</span>
                    <Switch
                      checked={hour.open}
                      onCheckedChange={(open) => updateHour(hour.day, { open })}
                      aria-label={`Open on ${label}`}
                    />
                  </div>
                  {hour.open ? (
                    <>
                      <TimeSelect
                        label={`${label} opens at`}
                        value={hour.opensAt}
                        invalid={invalid}
                        onChange={(opensAt) => updateHour(hour.day, { opensAt })}
                      />
                      <TimeSelect
                        label={`${label} closes at`}
                        value={hour.closesAt}
                        invalid={invalid}
                        onChange={(closesAt) => updateHour(hour.day, { closesAt })}
                      />
                    </>
                  ) : (
                    <span className="col-span-2 text-right text-[13px] text-placeholder">Closed all day</span>
                  )}
                </div>
              );
            })}
            {hoursError && (
              <p id="info-hours-error" role="alert" className="pt-2 text-xs text-danger">
                {hoursError}
              </p>
            )}
          </fieldset>
        </InfoCard>
      </div>
    </form>
  );
}

function Field({
  id,
  label,
  optional,
  error,
  children,
}: {
  id: string;
  label: string;
  optional?: boolean;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div>
      <label htmlFor={id} className="block pb-1.5 text-[13px] leading-5 font-semibold text-foreground">
        {label}
        {optional && <span className="ml-1 font-normal text-placeholder">(optional)</span>}
      </label>
      {children}
      {error && (
        <p id={`${id}-error`} role="alert" className="pt-1 text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  );
}

function TimeSelect({
  label,
  value,
  invalid,
  onChange,
}: {
  label: string;
  value: string;
  invalid: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <SelectField
      aria-label={label}
      value={value}
      aria-invalid={invalid}
      onValueChange={onChange}
      options={TIME_OPTIONS.map((time) => ({ value: time, label: formatClock(time) }))}
      className="h-[38px] gap-1 rounded-[10px] border-transparent bg-panel pr-2 pl-2.5 text-sm [&_svg]:size-3.5"
    />
  );
}
