import { createContext } from "react";
import type { AuthResponse, User } from "../types/api";

export interface AuthContextValue {
  user: User | null;
  loading: boolean;
  // identifier: a local account's email, or a Navidrome username.
  login: (identifier: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  // Lets the profile page apply an avatar/email edit's response immediately (header email,
  // etc. update without a refetch) without every page needing its own copy of "current user".
  setUser: (user: User) => void;
  // A password change issues a fresh session token (the old one is revoked server-side by the
  // token_version bump), so it needs the same token+user swap login/register do internally.
  applySession: (result: AuthResponse) => void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);
