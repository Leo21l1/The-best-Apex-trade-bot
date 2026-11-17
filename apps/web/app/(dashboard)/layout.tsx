import { AppShell } from "@/components/shell/AppShell";
import { RealtimeProvider } from "@/hooks/useRealtime";
import { SessionProvider } from "@/hooks/useSession";
import { SystemStatusProvider } from "@/hooks/useSystemStatus";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <RealtimeProvider>
        <SystemStatusProvider>
        <AppShell>{children}</AppShell>
        </SystemStatusProvider>
      </RealtimeProvider>
    </SessionProvider>
  );
}
