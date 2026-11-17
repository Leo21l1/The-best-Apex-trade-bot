import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, EmptyState, Panel } from "@/components/ui/primitives";
import { navByPath } from "@/lib/nav";

/**
 * Honest placeholder for modules scheduled in later phases. It never renders sample numbers,
 * so nothing on these pages can be mistaken for real account or market data.
 */
export function PlannedFeature({
  path,
  capabilities,
  dependsOn,
}: {
  path: string;
  capabilities: string[];
  dependsOn: string[];
}) {
  const item = navByPath(path);
  return (
    <>
      <PageHeader path={path} />
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <Panel title="Status">
            <EmptyState title={`Not available yet — scheduled for Phase ${item?.phase ?? "?"}`}>
              No data is displayed here until the underlying engine is implemented and verified.
              Nothing on this page is simulated.
            </EmptyState>
          </Panel>
        </div>
        <Panel title="Requires">
          <ul className="space-y-1.5 text-sm text-muted">
            {dependsOn.map((d) => (
              <li key={d} className="flex gap-2">
                <span className="text-faint">•</span>
                {d}
              </li>
            ))}
          </ul>
        </Panel>
        <div className="lg:col-span-3">
          <Panel title="Planned capabilities">
            <ul className="grid gap-x-6 gap-y-1.5 text-sm text-muted sm:grid-cols-2">
              {capabilities.map((c) => (
                <li key={c} className="flex gap-2">
                  <span className="text-faint">—</span>
                  {c}
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      </div>
      <div className="mt-4">
        <Callout tone="info" title="Analysis only">
          Any future signal shown on this page is advisory. Orders are placed manually by you in
          Tradovate.
        </Callout>
      </div>
    </>
  );
}
