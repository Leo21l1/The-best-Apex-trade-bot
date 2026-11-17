import { AppShell } from "@/components/shell/AppShell";
import { SessionProvider } from "@/hooks/useSession";
import { SystemStatusProvider } from "@/hooks/useSystemStatus";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <SystemStatusProvider>
        <AppShell>{children}</AppShell>
      </SystemStatusProvider>
    </SessionProvider>
  );
}
