import { Skeleton } from "@/components/ui/skeleton";

export function ModelCardSkeleton() {
  return (
    <div className="rounded-xl border bg-card p-5 space-y-3">
      <div className="flex items-center justify-between">
        <Skeleton className="h-5 w-36" />
        <Skeleton className="h-5 w-16 rounded-full" />
      </div>
      <Skeleton className="h-4 w-48" />
      <div className="grid grid-cols-2 gap-2 pt-1">
        <Skeleton className="h-10 rounded-md" />
        <Skeleton className="h-10 rounded-md" />
      </div>
      <Skeleton className="h-8 w-full rounded-md" />
    </div>
  );
}
