"use client";

interface DiffFile {
  header: string;
  hunks: DiffHunk[];
}

interface DiffHunk {
  header: string;
  lines: DiffLine[];
}

interface DiffLine {
  type: "add" | "remove" | "context";
  content: string;
  oldLineNum: number | null;
  newLineNum: number | null;
}

function parseDiff(diff: string): DiffFile[] {
  const files: DiffFile[] = [];
  const lines = diff.split("\n");
  let currentFile: DiffFile | null = null;
  let currentHunk: DiffHunk | null = null;
  let oldLine = 0;
  let newLine = 0;

  for (const line of lines) {
    // File header: diff --git a/... b/...
    if (line.startsWith("diff --git") || line.startsWith("--- ") || line.startsWith("+++ ")) {
      if (line.startsWith("diff --git")) {
        if (currentFile) files.push(currentFile);
        currentFile = { header: line, hunks: [] };
        currentHunk = null;
      } else if (currentFile) {
        // Append --- and +++ lines to the header
        currentFile.header += "\n" + line;
      }
      continue;
    }

    // Hunk header: @@ -oldStart,oldCount +newStart,newCount @@
    const hunkMatch = line.match(/^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@(.*)$/);
    if (hunkMatch) {
      oldLine = parseInt(hunkMatch[1], 10);
      newLine = parseInt(hunkMatch[2], 10);
      currentHunk = { header: line, lines: [] };
      if (currentFile) currentFile.hunks.push(currentHunk);
      continue;
    }

    if (!currentHunk) continue;

    if (line.startsWith("+")) {
      currentHunk.lines.push({
        type: "add",
        content: line.slice(1),
        oldLineNum: null,
        newLineNum: newLine++,
      });
    } else if (line.startsWith("-")) {
      currentHunk.lines.push({
        type: "remove",
        content: line.slice(1),
        oldLineNum: oldLine++,
        newLineNum: null,
      });
    } else if (line.startsWith(" ") || line === "") {
      currentHunk.lines.push({
        type: "context",
        content: line.startsWith(" ") ? line.slice(1) : line,
        oldLineNum: oldLine++,
        newLineNum: newLine++,
      });
    }
  }

  if (currentFile) files.push(currentFile);
  return files;
}

interface DiffViewerProps {
  diff: string;
}

export default function DiffViewer({ diff }: DiffViewerProps) {
  if (!diff.trim()) {
    return (
      <div className="flex h-48 items-center justify-center rounded-lg border border-zinc-800 bg-zinc-950">
        <p className="text-sm text-zinc-500">No diff available</p>
      </div>
    );
  }

  const files = parseDiff(diff);

  return (
    <div className="space-y-4 font-mono text-sm">
      {files.map((file, fileIdx) => (
        <div
          key={fileIdx}
          className="overflow-hidden rounded-lg border border-zinc-800 bg-zinc-950"
        >
          {/* File header */}
          <div className="border-b border-zinc-800 bg-zinc-900 px-4 py-2">
            <span className="text-xs text-zinc-400">
              {file.header.split("\n")[0]}
            </span>
          </div>

          {/* Hunks */}
          {file.hunks.map((hunk, hunkIdx) => (
            <div key={hunkIdx}>
              {/* Hunk header */}
              <div className="border-b border-zinc-800 bg-zinc-900/50 px-4 py-1">
                <span className="text-xs text-blue-400">{hunk.header}</span>
              </div>

              {/* Lines */}
              <table className="w-full border-collapse">
                <tbody>
                  {hunk.lines.map((line, lineIdx) => {
                    const bgClass =
                      line.type === "add"
                        ? "bg-green-950/40"
                        : line.type === "remove"
                          ? "bg-red-950/40"
                          : "";
                    const textClass =
                      line.type === "add"
                        ? "text-green-300"
                        : line.type === "remove"
                          ? "text-red-300"
                          : "text-zinc-400";
                    const prefix =
                      line.type === "add"
                        ? "+"
                        : line.type === "remove"
                          ? "-"
                          : " ";

                    return (
                      <tr key={lineIdx} className={bgClass}>
                        <td className="w-12 select-none border-r border-zinc-800 px-2 text-right text-xs text-zinc-600">
                          {line.oldLineNum ?? ""}
                        </td>
                        <td className="w-12 select-none border-r border-zinc-800 px-2 text-right text-xs text-zinc-600">
                          {line.newLineNum ?? ""}
                        </td>
                        <td className="select-none border-r border-zinc-800 px-2 text-center text-xs text-zinc-600">
                          {prefix}
                        </td>
                        <td className={`px-3 py-0.5 whitespace-pre ${textClass}`}>
                          {line.content}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
