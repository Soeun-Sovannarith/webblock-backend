import { redirect } from "next/navigation";
import { siteConfig } from "@/config/site";

/** This app is only the Owner Portal: open the demo website's products. */
export default function HomePage() {
  redirect(`/portal/${siteConfig.demoWebsiteId}/products`);
}
