import { Badge } from "@/components/ui/badge";

type Variant = "default" | "secondary" | "destructive" | "outline" | "success" | "warning";

interface StatusBadgeProps {
  status: string;
}

const STATUS_MAP: Record<string, { label: string; variant: Variant }> = {
  uploaded: { label: "Queued", variant: "secondary" },
  processing: { label: "Processing", variant: "warning" },
  completed: { label: "Completed", variant: "success" },
  failed: { label: "Failed", variant: "destructive" },
};

export function StatusBadge({ status }: StatusBadgeProps) {
  const meta = STATUS_MAP[status] ?? { label: status, variant: "outline" as Variant };
  return <Badge variant={meta.variant}>{meta.label}</Badge>;
}
