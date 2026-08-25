import { useMemo } from "react";

import { useUserDirectory } from "@/hooks/useUserDirectory";
import { User } from "../types";

/**
 * Shared users list for NAME DISPLAY — "Created By", "Approved By", owner columns.
 *
 * Sourced from the user directory (Nirmaan Users -> User -> Deleted Document), NOT
 * from a `Nirmaan Users` list. A `Nirmaan Users` fetch can only resolve people who
 * still work here, so every row created by someone since offboarded rendered their
 * raw login id — `szeeshan074@gmail.com` instead of `Zeeshan Shaikh`. The directory
 * recovers those names from the deleted account's tombstone.
 *
 * Every one of this hook's ~34 call sites is a LOOKUP (`.find(u => u.name === id)`),
 * never a picker, which is why returning offboarded users here is correct. **A picker
 * must not use this hook** — it would offer people who have left. Use
 * `useUserDirectory().activeUsers`, or `useProjectAssignees`, which is already scoped
 * to live profiles.
 *
 * The element type stays `User` for call-site compatibility. It was already wider than
 * the runtime objects — the previous fetch requested only name / full_name /
 * role_profile — and those three are exactly what the directory returns.
 */
export const useUsersList = () => {
    const { users, isLoading, error, mutate } = useUserDirectory();

    // `users` is already memoized upstream, so this identity is stable per fetch and
    // callers can keep it in a dependency array.
    const data = useMemo(() => users as unknown as User[], [users]);

    return { data, isLoading, error, mutate };
};
