import type { Profile } from "@/features/account/types";
import { OWNER_ID } from "./session";

export const profiles: Profile[] = [
  {
    id: OWNER_ID,
    fullName: "Sovannarith Soeun",
    initials: "SV",
    email: "sovannarith@gmail.com",
    avatarUrl: "/mock/avatars/sovannarith-soeun.jpg",
  },
];
