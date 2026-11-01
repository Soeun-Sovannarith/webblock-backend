import type { ReactNode } from "react";
import Link from "next/link";
import { PageHeader } from "@/components/common/page-header";
import { Button } from "@/components/ui/button";
import type { BusinessInfoView as BusinessInfoData } from "../service";
import {
  BusinessIdentity,
  BusinessMark,
  DAY_LABEL,
  hoursText,
  InfoCard,
  InfoCardTitle,
  SectionLabel,
  SiteBanner,
} from "./business-info-parts";
import { BusinessInfoForm } from "./business-info-form";

/** Owner Portal → Business information. `?edit=1` swaps the details for the form. */
export function BusinessInfoView({ data }: { data: BusinessInfoData }) {
  const { info, website } = data;

  if (data.editing) {
    return (
      <>
        <PageHeader
          className="mb-0"
          title="Business information"
          description="You’re editing your details. Changes go live on your next deploy."
        />
        <BusinessInfoForm info={info} website={website} />
      </>
    );
  }

  return (
    <>
      <PageHeader
        className="mb-0"
        title="Business information"
        description={
          website.domain ? `This is what customers see on ${website.domain}.` : "This is what customers see on your website."
        }
      />

      <InfoCard className="p-2.5">
        <SiteBanner website={website} />
        <div className="flex flex-wrap items-center gap-4 px-4 pt-4 pb-3">
          <BusinessMark name={website.name} logoUrl={info.logoUrl} />
          <div className="flex-1">
            <BusinessIdentity website={website} />
          </div>
          <Button asChild>
            <Link href="?edit=1" scroll={false}>
              Edit details
            </Link>
          </Button>
        </div>
      </InfoCard>

      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)]">
        <InfoCard>
          <InfoCardTitle>Business details</InfoCardTitle>
          <dl>
            <div>
              <SectionLabel>About</SectionLabel>
              <Detail label="Business description">{info.description}</Detail>
            </div>
            <div>
              <SectionLabel>Contact</SectionLabel>
              <Detail label="Phone number">{info.phone}</Detail>
              <Detail label="Email">{info.email}</Detail>
            </div>
            <div>
              <SectionLabel>Location</SectionLabel>
              <Detail label="Address">{info.address}</Detail>
              <Detail label="Google Maps link">
                {info.mapsUrl ? (
                  <a
                    href={info.mapsUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="break-all text-primary underline underline-offset-2 hover:text-brand-strong"
                  >
                    {info.mapsUrl}
                  </a>
                ) : (
                  <span className="text-placeholder">Not added</span>
                )}
              </Detail>
            </div>
          </dl>
        </InfoCard>

        <InfoCard>
          <InfoCardTitle>Opening hours</InfoCardTitle>
          <dl>
            {info.hours.map((hour) => {
              const text = hoursText(hour);
              return (
                <div
                  key={hour.day}
                  className="flex items-center justify-between gap-4 border-b border-border py-3.5 last:border-0"
                >
                  <dt className="text-sm text-foreground">{DAY_LABEL[hour.day]}</dt>
                  <dd className={text ? "text-sm text-foreground" : "text-sm text-placeholder"}>{text ?? "Closed"}</dd>
                </div>
              );
            })}
          </dl>
        </InfoCard>
      </div>
    </>
  );
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="border-border py-2.5 first-of-type:pt-0 not-last:border-b">
      <dt className="text-xs leading-[18px] text-muted-foreground">{label}</dt>
      <dd className="pt-1 text-sm leading-5 text-foreground">{children}</dd>
    </div>
  );
}
