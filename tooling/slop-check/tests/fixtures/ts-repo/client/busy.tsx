// The menu group keeps focus while busy; a bare menu item drops it, so the group wraps the item.
export function Busy({ label }: { label: string }) {
  return <p className="busy">Don't close this window, it's saving // {label}</p>;
}
