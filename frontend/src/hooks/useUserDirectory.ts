import { useFrappeGetCall } from "frappe-react-sdk";
import { useCallback, useMemo } from "react";

/**
 * One resolver for every place the UI shows a person's name.
 *
 * `owner`, `modified_by`, `completed_by` and friends store an EMAIL, not a link —
 * they are plain varchar columns with no DocField behind them, so nothing keeps
 * them in step with the user table. When an account is deleted the name goes with
 * it and those columns start rendering raw login ids.
 *
 * The backend resolves through Nirmaan Users -> User -> Deleted Document (the
 * tombstone, which still holds the deleted account's full_name), so this hook
 * covers people who no longer exist on the site. It replaces the per-page
 * `getUserName` closures built over a `Nirmaan Users` list, which can only ever
 * resolve people who are still here.
 *
 * Fetches the whole directory once (~200 rows) and matches locally, so a list
 * page resolves a hundred owners with no extra requests.
 */

export type UserNameSource = "profile" | "user" | "deleted";

export interface DirectoryUser {
  /** The email — this is what owner / modified_by / completed_by actually store. */
  name: string;
  full_name: string;
  role_profile: string | null;
  is_active: 0 | 1;
  /** `deleted` means the name came from a tombstone: this person is gone from the site. */
  source: UserNameSource;
}

interface GetUserDirectoryResponse {
  message: { users: DirectoryUser[] };
}

export const USER_DIRECTORY_SWR_KEY = "user-directory";

export const useUserDirectory = () => {
  const { data, isLoading, error, mutate } = useFrappeGetCall<GetUserDirectoryResponse>(
    "nirmaan_stack.api.users.get_user_directory",
    undefined,
    USER_DIRECTORY_SWR_KEY
  );

  const users = useMemo(() => data?.message?.users ?? [], [data]);

  const byEmail = useMemo(() => {
    const map = new Map<string, DirectoryUser>();
    users.forEach((user) => map.set(user.name, user));
    return map;
  }, [users]);

  /**
   * Drop-in for the existing local `getUserName` closures — same fallback chain
   * (`full_name || id || "--"`), so no call site has to change its handling.
   */
  const getUserName = useCallback(
    (id?: string) => (id ? byEmail.get(id)?.full_name || id : "--"),
    [byEmail]
  );

  /** True once the person is off the site — for a muted style or an "(inactive)" hint. */
  const isFormerUser = useCallback(
    (id?: string) => (id ? byEmail.get(id)?.is_active === 0 : false),
    [byEmail]
  );

  /** Only people who still work here — for pickers and assignee dropdowns, never for display. */
  const activeUsers = useMemo(() => users.filter((user) => user.is_active === 1), [users]);

  return { users, activeUsers, byEmail, getUserName, isFormerUser, isLoading, error, mutate };
};
