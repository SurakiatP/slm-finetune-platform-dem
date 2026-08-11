import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Download, Rocket, MessageSquare } from "lucide-react";
import type { TrainedModel } from "@/types";
import { taskTypeLabels, baseModelLabels } from "@/data/mockData";

const statusColor: Record<string, "default" | "secondary" | "outline"> = {
  deployed: "default",
  deploying: "secondary",
  ready: "outline",
};

export function ModelCard({ model }: { model: TrainedModel }) {
  return (
    <Link to={`/models/${model.id}`}>
      <Card className="hover:shadow-md transition-shadow cursor-pointer h-full">
        <CardHeader className="pb-3">
          <div className="flex items-start justify-between gap-2">
            <div>
              <CardTitle className="text-sm font-semibold font-mono">{model.name}</CardTitle>
              <p className="text-[10px] text-muted-foreground mt-0.5">{baseModelLabels[model.baseModel]}</p>
            </div>
            <Badge variant={statusColor[model.status]} className="text-[10px]">{model.status}</Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-3 gap-2 text-center">
            <div>
              <p className="text-lg font-bold text-foreground">{model.accuracy}%</p>
              <p className="text-[10px] text-muted-foreground">Accuracy</p>
            </div>
            <div>
              <p className="text-lg font-bold text-foreground">{model.f1Score}%</p>
              <p className="text-[10px] text-muted-foreground">F1 Score</p>
            </div>
            <div>
              <p className="text-lg font-bold text-foreground">{model.fileSize}</p>
              <p className="text-[10px] text-muted-foreground">Size</p>
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <Badge variant="outline" className="text-[10px]">{taskTypeLabels[model.taskType]}</Badge>
            <Badge variant="outline" className="text-[10px]">{model.format}</Badge>
          </div>
          <div className="flex gap-2 pt-1" onClick={(e) => e.preventDefault()}>
            <Button variant="outline" size="sm" className="flex-1 text-xs h-8" asChild>
              <Link to={`/models/${model.id}?tab=export`}>
                <Download className="h-3 w-3 mr-1" /> Export
              </Link>
            </Button>
            <Button variant="outline" size="sm" className="flex-1 text-xs h-8">
              <Rocket className="h-3 w-3 mr-1" /> Deploy
            </Button>
            <Button variant="outline" size="sm" className="h-8 px-2" asChild>
              <Link to="/playground">
                <MessageSquare className="h-3 w-3" />
              </Link>
            </Button>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
