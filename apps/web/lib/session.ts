/** CME Globex session state in New York time (holidays not modelled). Pure; unit-tested. */
export function sessionState(d: Date): { open: boolean; label: string } {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  const wd = get("weekday");
  const mins = (Number(get("hour")) % 24) * 60 + Number(get("minute"));
  const H = (h: number, m = 0) => h * 60 + m;
  let open: boolean;
  if (wd === "Sat") open = false;
  else if (wd === "Sun") open = mins >= H(18);
  else if (wd === "Fri") open = mins < H(17);
  else open = mins < H(17) || mins >= H(18);
  if (!open) return { open, label: "Market closed" };
  if (wd !== "Sat" && wd !== "Sun" && mins >= H(9, 30) && mins < H(16)) return { open, label: "Regular session" };
  return { open, label: "Overnight session" };
}
