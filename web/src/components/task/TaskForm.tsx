"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type Priority = "low" | "medium" | "high";

export interface ImageAttachment {
  file: File;
  preview: string;
}

export interface TaskFormData {
  description: string;
  priority: Priority;
  additionalContext: string;
  images: ImageAttachment[];
}

interface TaskFormProps {
  value: TaskFormData;
  onChange: (data: TaskFormData) => void;
}

const PRIORITY_OPTIONS: { value: Priority; label: string; color: string }[] = [
  { value: "low", label: "Low", color: "text-green-400 border-green-700 bg-green-950" },
  { value: "medium", label: "Medium", color: "text-yellow-400 border-yellow-700 bg-yellow-950" },
  { value: "high", label: "High", color: "text-red-400 border-red-700 bg-red-950" },
];

const MAX_DESCRIPTION_LENGTH = 4000;
const MAX_CONTEXT_LENGTH = 2000;
const MAX_IMAGES = 5;
const MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024; // 10MB

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function TaskForm({ value, onChange }: TaskFormProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const createdUrlsRef = useRef<string[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [imageError, setImageError] = useState<string | null>(null);

  // Clean up all created object URLs on unmount
  useEffect(() => {
    return () => {
      createdUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
    };
  }, []);

  const addImages = useCallback(
    (files: FileList | File[]) => {
      setImageError(null);
      const fileArray = Array.from(files);

      // Validate file count
      const remaining = MAX_IMAGES - value.images.length;
      if (remaining <= 0) {
        setImageError(`Maximum ${MAX_IMAGES} images allowed.`);
        return;
      }

      const messages: string[] = [];
      if (fileArray.length > remaining) {
        messages.push(
          `Can only add ${remaining} more image${remaining === 1 ? "" : "s"} (max ${MAX_IMAGES}).`
        );
      }

      const toAdd = fileArray.slice(0, remaining);
      const valid: ImageAttachment[] = [];

      for (const file of toAdd) {
        if (!file.type.startsWith("image/")) {
          messages.push(`"${file.name}" is not an image file.`);
          continue;
        }
        if (file.size > MAX_IMAGE_SIZE_BYTES) {
          messages.push(`"${file.name}" exceeds 10MB limit (${formatFileSize(file.size)}).`);
          continue;
        }
        const url = URL.createObjectURL(file);
        createdUrlsRef.current.push(url);
        valid.push({ file, preview: url });
      }

      if (messages.length > 0) {
        setImageError(messages.join(" "));
      }

      if (valid.length > 0) {
        onChange({ ...value, images: [...value.images, ...valid] });
      }
    },
    [value, onChange]
  );

  const removeImage = useCallback(
    (index: number) => {
      setImageError(null);
      const removed = value.images[index];
      if (removed) {
        URL.revokeObjectURL(removed.preview);
      }
      const updated = value.images.filter((_, i) => i !== index);
      onChange({ ...value, images: updated });
    },
    [value, onChange]
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      if (e.dataTransfer.files.length > 0) {
        addImages(e.dataTransfer.files);
      }
    },
    [addImages]
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
  }, []);

  const handleFileInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files && e.target.files.length > 0) {
        addImages(e.target.files);
      }
      // Reset the input so the same file can be selected again
      e.target.value = "";
    },
    [addImages]
  );

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-text-primary">Describe Your Task</h2>
        <p className="mt-1 text-sm text-text-tertiary">
          Tell Forge what you want to build, fix, or change.
        </p>
      </div>

      {/* Description textarea */}
      <div>
        <label htmlFor="task-description" className="block text-sm font-medium text-text-secondary">
          Task description
        </label>
        <textarea
          id="task-description"
          rows={6}
          value={value.description}
          onChange={(e) => onChange({ ...value, description: e.target.value })}
          maxLength={MAX_DESCRIPTION_LENGTH}
          placeholder="Build a REST API with user authentication, CRUD endpoints for posts, and unit tests..."
          className="mt-1 block w-full resize-y rounded-lg border border-border-color bg-surface-3 px-4 py-2 text-text-primary placeholder:text-text-dim focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
        />
        <div className="mt-1 flex items-center justify-between text-xs text-text-dim">
          <span>Supports markdown formatting</span>
          <span>
            {value.description.length}/{MAX_DESCRIPTION_LENGTH}
          </span>
        </div>
      </div>

      {/* Image upload section */}
      <div>
        <label className="block text-sm font-medium text-text-secondary">
          Images <span className="text-text-dim">(optional, max {MAX_IMAGES})</span>
        </label>

        {/* Drop zone */}
        <div
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onClick={() => value.images.length < MAX_IMAGES && fileInputRef.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              if (value.images.length < MAX_IMAGES) {
                fileInputRef.current?.click();
              }
            }
          }}
          style={{
            marginTop: "8px",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: "8px",
            padding: "24px",
            borderRadius: "var(--radius-md)",
            border: `2px dashed ${dragOver ? "var(--accent)" : "var(--border)"}`,
            background: dragOver ? "var(--accent-glow)" : "var(--bg-surface-3)",
            cursor: value.images.length >= MAX_IMAGES ? "not-allowed" : "pointer",
            transition: "var(--transition)",
            opacity: value.images.length >= MAX_IMAGES ? 0.5 : 1,
          }}
        >
          <svg
            style={{ width: "32px", height: "32px", color: dragOver ? "var(--accent)" : "var(--text-dim)" }}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={1.5}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M2.25 15.75l5.159-5.159a2.25 2.25 0 013.182 0l5.159 5.159m-1.5-1.5l1.409-1.409a2.25 2.25 0 013.182 0l2.909 2.909M3.75 21h16.5A2.25 2.25 0 0022.5 18.75V5.25A2.25 2.25 0 0020.25 3H3.75A2.25 2.25 0 001.5 5.25v13.5A2.25 2.25 0 003.75 21z"
            />
          </svg>
          <span style={{ fontSize: "13px", color: "var(--text-tertiary)" }}>
            Drag &amp; drop images here, or click to browse
          </span>
          <span style={{ fontSize: "11px", color: "var(--text-dim)" }}>
            PNG, JPG, GIF, WebP &middot; Max 10MB each
          </span>
        </div>

        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          multiple
          onChange={handleFileInputChange}
          style={{ display: "none" }}
        />

        {/* Validation error */}
        {imageError && (
          <div
            style={{
              marginTop: "8px",
              fontSize: "12px",
              color: "#fca5a5",
              padding: "8px 12px",
              borderRadius: "var(--radius-md)",
              border: "1px solid rgba(239,68,68,0.3)",
              background: "var(--red-dim)",
            }}
          >
            {imageError}
          </div>
        )}

        {/* Thumbnails */}
        {value.images.length > 0 && (
          <div
            style={{
              marginTop: "12px",
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))",
              gap: "12px",
            }}
          >
            {value.images.map((img, index) => (
              <div
                key={`${img.file.name}-${index}`}
                style={{
                  position: "relative",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--border)",
                  background: "var(--bg-surface-3)",
                  overflow: "hidden",
                }}
              >
                {/* Thumbnail image */}
                <div style={{ position: "relative", width: "100%", paddingTop: "100%" }}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={img.preview}
                    alt={img.file.name}
                    style={{
                      position: "absolute",
                      top: 0,
                      left: 0,
                      width: "100%",
                      height: "100%",
                      objectFit: "cover",
                    }}
                  />
                </div>

                {/* Remove button */}
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    removeImage(index);
                  }}
                  aria-label={`Remove ${img.file.name}`}
                  style={{
                    position: "absolute",
                    top: "4px",
                    right: "4px",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    width: "22px",
                    height: "22px",
                    borderRadius: "50%",
                    background: "rgba(0,0,0,0.7)",
                    border: "none",
                    color: "white",
                    cursor: "pointer",
                    fontSize: "14px",
                    lineHeight: 1,
                    padding: 0,
                  }}
                >
                  &times;
                </button>

                {/* File info */}
                <div style={{ padding: "6px 8px" }}>
                  <div
                    style={{
                      fontSize: "11px",
                      color: "var(--text-secondary)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                    title={img.file.name}
                  >
                    {img.file.name}
                  </div>
                  <div style={{ fontSize: "10px", color: "var(--text-dim)", marginTop: "2px" }}>
                    {formatFileSize(img.file.size)}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Priority selector */}
      <div>
        <label className="block text-sm font-medium text-text-secondary">
          Priority <span className="text-text-dim">(optional)</span>
        </label>
        <div className="mt-2 flex gap-3">
          {PRIORITY_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => onChange({ ...value, priority: option.value })}
              className={`rounded-lg border px-4 py-1.5 text-sm font-medium transition ${
                value.priority === option.value
                  ? option.color
                  : "border-border-color bg-surface-3 text-text-tertiary hover:border-border-color/80"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {/* Additional context textarea */}
      <div>
        <label htmlFor="task-context" className="block text-sm font-medium text-text-secondary">
          Additional context <span className="text-text-dim">(optional)</span>
        </label>
        <textarea
          id="task-context"
          rows={3}
          value={value.additionalContext}
          onChange={(e) => onChange({ ...value, additionalContext: e.target.value })}
          maxLength={MAX_CONTEXT_LENGTH}
          placeholder="Preferred libraries, coding style, or constraints..."
          className="mt-1 block w-full resize-y rounded-lg border border-border-color bg-surface-3 px-4 py-2 text-text-primary placeholder:text-text-dim focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
        />
        <div className="mt-1 text-right text-xs text-text-dim">
          {value.additionalContext.length}/{MAX_CONTEXT_LENGTH}
        </div>
      </div>
    </div>
  );
}
