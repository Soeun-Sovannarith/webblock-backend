import type { SessionUser } from "@/lib/auth/roles";

export const OWNER_ID = "0f8a2c4e-5b1d-4e7a-9c3f-2d6b8e1a7c90";
export const PETAL_AND_STEM_ID = "7d3e9b2a-1c4f-4a8e-b6d5-3f2e1a9c8b70";

/** The website owner this standalone portal always runs as (there is no sign-in page here). */
export const sessionUser: SessionUser = {
  id: OWNER_ID,
  email: "sovannarith@gmail.com",
  fullName: "Sovannarith Soeun",
  role: "USER",
  avatarUrl: "/mock/avatars/sovannarith-soeun.jpg",
};

type WebsiteOwner = { websiteId: string; ownerId: string };

/** Which user owns which website. */
export const websiteOwners: WebsiteOwner[] = [{ websiteId: PETAL_AND_STEM_ID, ownerId: OWNER_ID }];
