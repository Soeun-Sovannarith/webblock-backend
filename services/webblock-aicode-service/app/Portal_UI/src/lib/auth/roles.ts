export type Role = "USER" | "ADMIN";

export type SessionUser = {
  id: string;
  email: string;
  fullName: string;
  role: Role;
  avatarUrl: string | null;
};
