export type PuckBlock = {
  type: string;
  props: Record<string, unknown> & { id?: string };
};

export type PuckData = {
  root: { props?: { title?: string } & Record<string, unknown> };
  content: PuckBlock[];
};

export type SitePage = {
  path: string;
  name: string;
  seo_title?: string | null;
  seo_description?: string | null;
  data: PuckData;
};

export type SiteData = {
  pages: SitePage[];
};
