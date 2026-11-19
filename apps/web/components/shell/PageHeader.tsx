import { navByPath } from "@/lib/nav";

export function PageHeader({ path, children }: { path: string; children?: React.ReactNode }) {
  const item = navByPath(path);
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{item?.label ?? path}</h1>
        {item && <p className="mt-1 text-sm text-muted">{item.summary}</p>}
      </div>
      {children}
    </div>
  );
}
