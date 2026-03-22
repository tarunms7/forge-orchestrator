"use client";

import React from "react";

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
      <div
        style={{
          display: "flex",
          height: "12rem",
          alignItems: "center",
          justifyContent: "center",
          borderRadius: "0.5rem",
          border: "1px solid var(--border-subtle)",
          background: "var(--bg-surface-1)",
        }}
      >
        <p style={{ fontSize: "0.875rem", color: "var(--text-secondary)" }}>No diff available</p>
      </div>
    );
  }

  const files = parseDiff(diff);

  const lineNumStyle: React.CSSProperties = {
    width: "3rem",
    userSelect: "none",
    borderRight: "1px solid var(--border-subtle)",
    padding: "0 0.5rem",
    textAlign: "right",
    fontSize: "0.75rem",
    color: "color-mix(in srgb, var(--text-secondary) 55%, transparent)",
  };

  const prefixStyle: React.CSSProperties = {
    userSelect: "none",
    borderRight: "1px solid var(--border-subtle)",
    padding: "0 0.5rem",
    textAlign: "center",
    fontSize: "0.75rem",
    color: "color-mix(in srgb, var(--text-secondary) 55%, transparent)",
  };

  return (
    <div
      style={{ display: "flex", flexDirection: "column", gap: "1rem" }}
      className="font-mono text-sm"
    >
      {files.map((file, fileIdx) => (
        <div
          key={fileIdx}
          style={{
            overflow: "hidden",
            borderRadius: "0.5rem",
            border: "1px solid var(--border-subtle)",
            background: "var(--bg-surface-1)",
          }}
        >
          {/* File header */}
          <div
            style={{
              borderBottom: "1px solid var(--border-subtle)",
              background: "var(--bg-surface-2)",
              padding: "0.5rem 1rem",
            }}
          >
            <span style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>
              {file.header.split("\n")[0]}
            </span>
          </div>

          {/* Hunks */}
          {file.hunks.map((hunk, hunkIdx) => (
            <div key={hunkIdx}>
              {/* Hunk header */}
              <div
                style={{
                  borderBottom: "1px solid var(--border-subtle)",
                  background: "color-mix(in srgb, var(--bg-surface-2) 50%, transparent)",
                  padding: "0.25rem 1rem",
                }}
              >
                <span style={{ fontSize: "0.75rem", color: "var(--accent)" }}>
                  {hunk.header}
                </span>
              </div>

              {/* Lines */}
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <tbody>
                  {hunk.lines.map((line, lineIdx) => {
                    const rowBg =
                      line.type === "add"
                        ? "color-mix(in srgb, var(--green) 15%, transparent)"
                        : line.type === "remove"
                          ? "color-mix(in srgb, var(--red) 15%, transparent)"
                          : "transparent";
                    const textColor =
                      line.type === "add"
                        ? "var(--green)"
                        : line.type === "remove"
                          ? "var(--red)"
                          : "var(--text-secondary)";
                    const prefix =
                      line.type === "add" ? "+" : line.type === "remove" ? "-" : " ";

                    return (
                      <tr key={lineIdx} style={{ background: rowBg }}>
                        <td style={lineNumStyle}>{line.oldLineNum ?? ""}</td>
                        <td style={lineNumStyle}>{line.newLineNum ?? ""}</td>
                        <td style={prefixStyle}>{prefix}</td>
                        <td
                          style={{
                            padding: "0.125rem 0.75rem",
                            whiteSpace: "pre",
                            color: textColor,
                          }}
                        >
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
