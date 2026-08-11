import { useRef } from "react";
import { Button } from "@/components/ui/button";
import { Upload, FileText, X, FileSpreadsheet, FileCode } from "lucide-react";
import type { ProjectFormData } from "@/pages/NewProject";

const fileIcons: Record<string, React.ElementType> = {
  csv: FileSpreadsheet,
  json: FileCode,
  jsonl: FileCode,
};

export function DataUploadStep({
  formData,
  updateForm,
}: {
  formData: ProjectFormData;
  updateForm: (p: Partial<ProjectFormData>) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFiles = (fileList: FileList | null) => {
    if (!fileList) return;
    const newFiles = Array.from(fileList);
    updateForm({ files: [...formData.files, ...newFiles] });
  };

  const removeFile = (index: number) => {
    updateForm({ files: formData.files.filter((_, i) => i !== index) });
  };

  const getIcon = (name: string) => {
    const ext = name.split(".").pop()?.toLowerCase() || "";
    const Icon = fileIcons[ext] || FileText;
    return <Icon className="h-4 w-4" />;
  };

  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm font-semibold text-foreground">Upload Training Data</p>
        <p className="text-xs text-muted-foreground mt-0.5">
          Upload few-shot examples or domain knowledge. Supported: CSV, JSON, JSONL (optional)
        </p>
      </div>

      {/* Drop zone */}
      <div
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); }}
        onDrop={(e) => { e.preventDefault(); e.stopPropagation(); handleFiles(e.dataTransfer.files); }}
        className="border-2 border-dashed border-border rounded-lg p-10 text-center cursor-pointer hover:border-primary/50 hover:bg-accent/50 transition-colors"
      >
        <Upload className="h-8 w-8 text-muted-foreground mx-auto mb-3" />
        <p className="text-sm font-medium text-foreground">Click to upload or drag & drop</p>
        <p className="text-xs text-muted-foreground mt-1">CSV, JSON, JSONL — up to 50MB</p>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".csv,.json,.jsonl"
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
      </div>

      {/* File list */}
      {formData.files.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-muted-foreground">{formData.files.length} file(s) selected</p>
          {formData.files.map((file, i) => (
            <div key={i} className="flex items-center gap-3 p-2.5 rounded-md bg-secondary/50 border border-border">
              <div className="text-muted-foreground">{getIcon(file.name)}</div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-foreground truncate">{file.name}</p>
                <p className="text-[10px] text-muted-foreground">{(file.size / 1024).toFixed(1)} KB</p>
              </div>
              <Button variant="ghost" size="icon" className="h-7 w-7 shrink-0" onClick={() => removeFile(i)}>
                <X className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))}
        </div>
      )}

      {/* Format guide */}
      <div className="bg-accent/50 rounded-lg p-4 space-y-2">
        <p className="text-xs font-semibold text-foreground">Expected Format</p>
        <div className="bg-background rounded-md p-3 font-mono text-[11px] text-muted-foreground overflow-x-auto">
          <pre>{`// JSONL format (one example per line)
{"input": "My bill is wrong", "output": "billing"}
{"input": "App keeps crashing", "output": "technical"}

// CSV format
input,output
"My bill is wrong","billing"
"App keeps crashing","technical"`}</pre>
        </div>
      </div>
    </div>
  );
}
