"use client";

export type ExecutionTarget = "local" | "remote";

export interface ExecutionConfig {
  target: ExecutionTarget;
  sshHost?: string;
  sshUser?: string;
  sshKeyPath?: string;
  sshPort?: number;
}

interface ExecutionTargetSelectorProps {
  value: ExecutionConfig;
  onChange: (config: ExecutionConfig) => void;
}

function LocalHealthIndicator() {
  return (
    <div className="mt-4 flex items-center gap-3 rounded-lg border border-border-color bg-surface-1 p-4">
      <div className="relative flex h-3 w-3">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-400 opacity-75" />
        <span className="relative inline-flex h-3 w-3 rounded-full bg-green-500" />
      </div>
      <div>
        <div className="text-sm font-medium text-text-primary">Local environment ready</div>
        <div className="text-xs text-text-tertiary">
          Tasks will run on this machine using your local tools and configs.
        </div>
      </div>
    </div>
  );
}

export default function ExecutionTargetSelector({
  value,
  onChange,
}: ExecutionTargetSelectorProps) {
  function handleTargetChange(target: ExecutionTarget) {
    if (target === "local") {
      onChange({ target });
    } else {
      onChange({ target, sshHost: "", sshUser: "", sshKeyPath: "", sshPort: 22 });
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-text-primary">Execution Target</h2>
        <p className="mt-1 text-sm text-text-tertiary">
          Choose where to run your task.
        </p>
      </div>

      {/* Toggle */}
      <div className="flex rounded-lg border border-border-color bg-surface-1 p-1">
        <button
          type="button"
          onClick={() => handleTargetChange("local")}
          className={`flex-1 rounded-md px-4 py-2 text-sm font-medium transition ${
            value.target === "local"
              ? "bg-surface-4 text-text-primary"
              : "text-text-tertiary hover:text-text-secondary"
          }`}
        >
          Local
        </button>
        <button
          type="button"
          onClick={() => handleTargetChange("remote")}
          className={`flex-1 rounded-md px-4 py-2 text-sm font-medium transition ${
            value.target === "remote"
              ? "bg-surface-4 text-text-primary"
              : "text-text-tertiary hover:text-text-secondary"
          }`}
        >
          Remote (SSH)
        </button>
      </div>

      {/* Local health check */}
      {value.target === "local" && <LocalHealthIndicator />}

      {/* Remote SSH config */}
      {value.target === "remote" && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label htmlFor="ssh-host" className="block text-sm font-medium text-text-secondary">
                Host
              </label>
              <input
                id="ssh-host"
                type="text"
                value={value.sshHost || ""}
                onChange={(e) => onChange({ ...value, sshHost: e.target.value })}
                placeholder="192.168.1.100"
                className="mt-1 block w-full rounded-lg border border-border-color bg-surface-3 px-4 py-2 text-text-primary placeholder:text-text-dim focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </div>
            <div>
              <label htmlFor="ssh-user" className="block text-sm font-medium text-text-secondary">
                User
              </label>
              <input
                id="ssh-user"
                type="text"
                value={value.sshUser || ""}
                onChange={(e) => onChange({ ...value, sshUser: e.target.value })}
                placeholder="ubuntu"
                className="mt-1 block w-full rounded-lg border border-border-color bg-surface-3 px-4 py-2 text-text-primary placeholder:text-text-dim focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label htmlFor="ssh-key" className="block text-sm font-medium text-text-secondary">
                SSH key path
              </label>
              <input
                id="ssh-key"
                type="text"
                value={value.sshKeyPath || ""}
                onChange={(e) => onChange({ ...value, sshKeyPath: e.target.value })}
                placeholder="~/.ssh/id_rsa"
                className="mt-1 block w-full rounded-lg border border-border-color bg-surface-3 px-4 py-2 text-text-primary placeholder:text-text-dim focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </div>
            <div>
              <label htmlFor="ssh-port" className="block text-sm font-medium text-text-secondary">
                Port
              </label>
              <input
                id="ssh-port"
                type="number"
                value={value.sshPort ?? 22}
                onChange={(e) => onChange({ ...value, sshPort: parseInt(e.target.value, 10) || 22 })}
                placeholder="22"
                className="mt-1 block w-full rounded-lg border border-border-color bg-surface-3 px-4 py-2 text-text-primary placeholder:text-text-dim focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
