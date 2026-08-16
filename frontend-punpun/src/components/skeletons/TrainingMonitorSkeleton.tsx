import { Skeleton } from "@/components/ui/skeleton";

export function TrainingMonitorSkeleton() {
  return (
    <div className="space-y-6 max-w-6xl">
      <div className="flex items-center gap-3">
        <Skeleton className="h-9 w-9 rounded-md" />
        <div>
          <Skeleton className="h-6 w-48 mb-1" />
          <Skeleton className="h-4 w-40" />
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-16 rounded-xl" />
        ))}
      </div>

      <Skeleton className="h-12 rounded-xl" />

      <div>
        <Skeleton className="h-9 w-80 rounded-md mb-4" />
        <Skeleton className="h-72 rounded-xl" />
      </div>
    </div>
  );
}
