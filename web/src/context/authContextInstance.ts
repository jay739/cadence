import { createContext } from "react";
import type { User } from "../types/api";

export interface AuthContextValue {
  user: User | null;
  loading: boolean;
  // identifier: a local account's email, or a Navidrome username.
  login: (identifier: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);
