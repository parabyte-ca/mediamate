import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { Check, Users } from "lucide-react";
import { api, PERSON_KEY, currentPersonId, type Person } from "./api/client";

/** Who's using this phone or browser. Everyone shares one watchlist; each person
 *  sees the shows they follow ("Mine") and gets alerts for those. */
interface PeopleCtx {
  people: Person[];
  me: Person | null;
  choose: (id: number) => void;
  reload: () => Promise<void>;
}

const Ctx = createContext<PeopleCtx>({ people: [], me: null, choose: () => {}, reload: async () => {} });
export const usePeople = () => useContext(Ctx);

async function linkPushDevice(personId: number) {
  try {
    const reg = await navigator.serviceWorker?.getRegistration();
    const sub = await reg?.pushManager.getSubscription();
    if (sub) await api.push.setPerson(sub.endpoint, personId);
  } catch { /* push not set up on this device */ }
}

export function PeopleProvider({ children }: { children: ReactNode }) {
  const [people, setPeople] = useState<Person[]>([]);
  const [meId, setMeId] = useState<number | null>(currentPersonId());
  const [asking, setAsking] = useState(false);

  const reload = useCallback(async () => {
    try {
      const list = await api.people.list();
      setPeople(list);
      const stored = currentPersonId();
      if (list.length === 1) {                       // just one person: it's them
        if (stored !== list[0].id) { try { localStorage.setItem(PERSON_KEY, String(list[0].id)); } catch { /* */ } }
        setMeId(list[0].id);
      } else if (!list.some((p) => p.id === stored)) {
        setAsking(true);                              // several people and this device hasn't said who
      }
    } catch { /* offline: keep going without people */ }
  }, []);
  useEffect(() => { void reload(); }, [reload]);

  const choose = useCallback((id: number) => {
    const changed = id !== currentPersonId();
    try { localStorage.setItem(PERSON_KEY, String(id)); } catch { /* private mode */ }
    setMeId(id);
    setAsking(false);
    void linkPushDevice(id);
    if (changed) window.location.reload();           // every list reloads for the new person
  }, []);

  const me = people.find((p) => p.id === meId) ?? null;
  return (
    <Ctx.Provider value={{ people, me, choose, reload }}>
      {children}
      {asking && people.length > 1 && <WhoPicker people={people} onPick={choose} />}
    </Ctx.Provider>
  );
}

export function Avatar({ person, size = 28 }: { person: Person; size?: number }) {
  const src = api.people.avatarSrc(person);
  if (src) {
    return <img src={src} alt={person.name} title={person.name} width={size} height={size}
                className="inline-block shrink-0 rounded-full object-cover" style={{ width: size, height: size }} />;
  }
  return (
    <span className="inline-flex shrink-0 items-center justify-center rounded-full font-semibold text-white"
          style={{ background: person.color, width: size, height: size, fontSize: size * 0.45 }}
          title={person.name} aria-label={person.name}>
      {person.name.slice(0, 1).toUpperCase()}
    </span>
  );
}

function WhoPicker({ people, onPick }: { people: Person[]; onPick: (id: number) => void }) {
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4" role="dialog" aria-modal aria-label="Who's using this device?">
      <div className="w-full max-w-sm rounded-2xl bg-white p-6 text-center shadow-xl dark:bg-gray-900">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Who's using this device?</h2>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Your watchlist and alerts follow your choice. You can switch any time from the menu.</p>
        <div className="mt-5 flex flex-wrap justify-center gap-4">
          {people.map((p) => (
            <button key={p.id} onClick={() => onPick(p.id)} className="flex w-20 flex-col items-center gap-2 rounded-xl p-2 hover:bg-gray-100 dark:hover:bg-gray-800">
              <Avatar person={p} size={56} />
              <span className="w-full truncate text-sm font-medium text-gray-800 dark:text-gray-200">{p.name}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

/** The current person, with a menu to switch. Hidden until there are two people. */
export function PersonSwitcher({ compact = false }: { compact?: boolean }) {
  const { people, me, choose } = usePeople();
  const [open, setOpen] = useState(false);
  if (people.length < 2) return null;
  return (
    <div className="relative" data-tour="person">
      <button onClick={() => setOpen((o) => !o)} aria-label="Switch person" aria-expanded={open}
              className={`flex items-center gap-2 rounded-full ${compact ? "" : "pr-2"} hover:bg-gray-100 dark:hover:bg-gray-800`}>
        {me ? <Avatar person={me} size={compact ? 26 : 28} /> : <Users className="h-5 w-5 text-gray-500" />}
        {!compact && me && <span className="max-w-[8rem] truncate text-sm font-medium text-gray-700 dark:text-gray-300">{me.name}</span>}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute left-0 z-50 mt-1 w-48 rounded-xl border border-gray-200 bg-white py-1 shadow-lg dark:border-gray-700 dark:bg-gray-900">
            <p className="px-3 py-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400">This device is</p>
            {people.map((p) => (
              <button key={p.id} onClick={() => { setOpen(false); choose(p.id); }}
                      className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-800">
                <Avatar person={p} size={22} />
                <span className="flex-1 truncate text-gray-800 dark:text-gray-200">{p.name}</span>
                {me?.id === p.id && <Check className="h-4 w-4 text-blue-600" />}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
