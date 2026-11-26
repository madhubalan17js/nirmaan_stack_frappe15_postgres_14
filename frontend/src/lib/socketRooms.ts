// How many holders each room has, per socket. The server keeps one membership per socket, so a room's leave message
// is sent only when its last holder lets go. Every part of the app that joins a doctype or document room on the
// shared socket counts here (the safe listeners in `@/lib/frappe` and the `listUpdateHub`), so one of them leaving
// can never cut the others out of the room. A reconnect starts the socket afresh on the server; each holder then
// sends its join again, and the counts stay as they are.
const roomHolders = new WeakMap<object, Map<string, number>>();

/** Counts one more holder of `room` on `socket`; sends `join` when it is the first. Returns the release, which sends
 * `leave` when it was the last. */
export function holdRoom(socket: object, room: string, join: () => void, leave: () => void): () => void {
  let holders = roomHolders.get(socket);
  if (!holders) roomHolders.set(socket, (holders = new Map()));
  const count = holders.get(room) ?? 0;
  holders.set(room, count + 1);
  if (count === 0) join();
  return () => {
    const left = (holders.get(room) ?? 1) - 1;
    if (left > 0) {
      holders.set(room, left);
      return;
    }
    holders.delete(room);
    leave();
  };
}
