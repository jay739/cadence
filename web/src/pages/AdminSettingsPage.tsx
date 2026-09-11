import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import type {
  IntegrationSettings,
  SettingsFieldValue,
  SettingsIntegrationName,
  SettingsSourcePreference,
  SourcePreference,
  TestConnectionResult,
} from "../types/api";
import "./AdminSettingsPage.css";

interface FieldSpec {
  name: string;
  label: string;
  placeholder?: string;
  type?: "text" | "password";
}

interface IntegrationSpec {
  key: SettingsIntegrationName;
  title: string;
  description: string;
  fields: FieldSpec[];
  testable: boolean;
}

const INTEGRATIONS: IntegrationSpec[] = [
  {
    key: "prowlarr",
    title: "Prowlarr",
    description: "Your indexer manager: finds the requested song on the indexers you already use.",
    fields: [
      { name: "prowlarr_url", label: "URL", placeholder: "http://prowlarr:9696" },
      { name: "prowlarr_api_key", label: "API key", type: "password" },
    ],
    testable: true,
  },
  {
    key: "qbittorrent",
    title: "qBittorrent",
    description: "Your download client: grabs found releases and reports when they finish.",
    fields: [
      { name: "qbittorrent_url", label: "URL", placeholder: "http://qbittorrent:8080" },
      { name: "qbittorrent_username", label: "Username" },
      { name: "qbittorrent_password", label: "Password", type: "password" },
    ],
    testable: true,
  },
  {
    key: "slskd",
    title: "slskd (Soulseek)",
    description: "Soulseek, the backup source used when your indexers come up empty.",
    fields: [
      { name: "slskd_url", label: "URL", placeholder: "http://slskd:5030" },
      { name: "slskd_api_key", label: "API key", type: "password" },
    ],
    testable: true,
  },
  {
    key: "spotify",
    title: "Spotify",
    description: "Powers the Spotify tab: catalog search plus the per-user account connection.",
    fields: [
      { name: "spotify_client_id", label: "Client ID" },
      { name: "spotify_client_secret", label: "Client secret", type: "password" },
    ],
    testable: true,
  },
  {
    key: "library",
    title: "Library",
    description: "Where downloaded songs are saved, and how their filenames are laid out.",
    fields: [
      { name: "music_library_path", label: "Library path", placeholder: "/app/library" },
      {
        name: "library_path_pattern",
        label: "Path pattern",
        placeholder: "{artist}/{title}.{ext}",
      },
    ],
    testable: false,
  },
];

function fieldsOf(settings: IntegrationSettings, integration: SettingsIntegrationName) {
  return settings[integration] as unknown as Record<string, SettingsFieldValue>;
}

function SourceTag({ source }: { source: SettingsFieldValue["source"] }) {
  if (source === "env") {
    return (
      <span
        className="settings-source settings-source--env"
        title="Set by an environment variable; overrides any saved value here"
      >
        env
      </span>
    );
  }
  if (source === "db") {
    return <span className="settings-source settings-source--db">saved</span>;
  }
  return <span className="settings-source settings-source--unset">not set</span>;
}

function SourcePreferenceToggle({
  integration,
  preference,
}: {
  integration: SettingsIntegrationName;
  preference: SourcePreference;
}) {
  const queryClient = useQueryClient();

  const setPreference = useMutation({
    mutationFn: (next: SettingsSourcePreference) =>
      api.adminSettings.updateSourcePreference(integration, next),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-settings"] }),
  });

  return (
    <fieldset className="settings-source-preference" disabled={setPreference.isPending}>
      <legend>Which source wins</legend>
      <label>
        <input
          type="radio"
          name={`${integration}-source-preference`}
          checked={preference.preference === "env"}
          onChange={() => setPreference.mutate("env")}
        />
        Use environment
      </label>
      <label>
        <input
          type="radio"
          name={`${integration}-source-preference`}
          checked={preference.preference === "database"}
          onChange={() => setPreference.mutate("database")}
        />
        Use these settings
      </label>
      {preference.forced_env && (
        <span className="form-note">
          SETTINGS_FORCE_ENV_PRECEDENCE is set, so every integration currently uses the environment
          regardless of this choice.
        </span>
      )}
    </fieldset>
  );
}

function IntegrationSection({
  spec,
  settings,
}: {
  spec: IntegrationSpec;
  settings: IntegrationSettings;
}) {
  const queryClient = useQueryClient();
  const currentFields = fieldsOf(settings, spec.key);
  const sourcePreference = settings.source_preferences[spec.key];

  const [draft, setDraft] = useState<Record<string, string>>({});
  const [testResult, setTestResult] = useState<TestConnectionResult | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  function valueFor(fieldName: string): string {
    if (fieldName in draft) return draft[fieldName];
    const current = currentFields[fieldName];
    // Never pre-fill a secret input with its masked display value; a save with that masked
    // string as the "new" value would overwrite the real secret with dots. Non-secret fields
    // are safe to prefill since their displayed value is the real one.
    if (current?.is_secret) return "";
    return current?.value ?? "";
  }

  const save = useMutation({
    mutationFn: (fields: Record<string, string>) => api.adminSettings.update(spec.key, fields),
    onSuccess: () => {
      setSaveError(null);
      setDraft({});
      queryClient.invalidateQueries({ queryKey: ["admin-settings"] });
    },
    onError: (err) => {
      setSaveError(err instanceof ApiError ? err.message : "Could not save these settings.");
    },
  });

  const test = useMutation({
    mutationFn: (fields: Record<string, string>) =>
      api.adminSettings.testConnection(spec.key, fields),
    onSuccess: (result) => setTestResult(result),
    onError: (err) => {
      setTestResult({
        ok: false,
        detail: err instanceof ApiError ? err.message : "Could not run the test.",
      });
    },
  });

  function fieldsForSubmit(): Record<string, string> {
    const payload: Record<string, string> = {};
    for (const field of spec.fields) {
      const value = draft[field.name];
      if (value !== undefined) {
        payload[field.name] = value;
      }
    }
    return payload;
  }

  function fieldsForTest(): Record<string, string> {
    // Test-connection needs every field's *effective* value, not only what changed in this
    // session, since testing an unmodified secret should still work against the saved value.
    const payload: Record<string, string> = {};
    for (const field of spec.fields) {
      const draftValue = draft[field.name];
      if (draftValue !== undefined) {
        payload[field.name] = draftValue;
        continue;
      }
      const current = currentFields[field.name];
      if (current && !current.is_secret && current.value) {
        payload[field.name] = current.value;
      }
    }
    return payload;
  }

  return (
    <section className="card settings-section">
      <div className="settings-section__header">
        <h2>{spec.title}</h2>
        <p>{spec.description}</p>
      </div>

      {sourcePreference && (
        <SourcePreferenceToggle integration={spec.key} preference={sourcePreference} />
      )}

      <div className="settings-fields">
        {spec.fields.map((field) => {
          const current = currentFields[field.name];
          return (
            <div className="field settings-field" key={field.name}>
              <label htmlFor={`${spec.key}-${field.name}`}>
                {field.label}
                {current && <SourceTag source={current.source} />}
              </label>
              <input
                id={`${spec.key}-${field.name}`}
                type={field.type === "password" ? "password" : "text"}
                placeholder={
                  current?.is_secret && current.value ? current.value : field.placeholder
                }
                value={valueFor(field.name)}
                disabled={current?.source === "env"}
                onChange={(e) => setDraft((prev) => ({ ...prev, [field.name]: e.target.value }))}
              />
              {current?.source === "env" && (
                <span className="form-note">
                  Set via environment variable; edit here has no effect until it's unset.
                </span>
              )}
            </div>
          );
        })}
      </div>

      {saveError && <p className="form-error">{saveError}</p>}
      {testResult && (
        <p className={testResult.ok ? "settings-test-ok" : "form-error"}>{testResult.detail}</p>
      )}

      <div className="settings-section__actions">
        {spec.testable && (
          <button
            type="button"
            className="button button--ghost"
            disabled={test.isPending}
            onClick={() => test.mutate(fieldsForTest())}
          >
            {test.isPending ? "Testing…" : "Test connection"}
          </button>
        )}
        <button
          type="button"
          className="button button--primary"
          disabled={save.isPending || Object.keys(draft).length === 0}
          onClick={() => save.mutate(fieldsForSubmit())}
        >
          {save.isPending ? "Saving…" : "Save"}
        </button>
      </div>
    </section>
  );
}

export function AdminSettingsPage() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["admin-settings"],
    queryFn: () => api.adminSettings.get(),
  });

  return (
    <div>
      <div className="page-header">
        <h1>Integration settings</h1>
        <p>
          Connect Prowlarr, qBittorrent, slskd, and Spotify, and set your library layout, without
          touching a config file or restarting the container. A value set by an environment variable
          always takes precedence over what's saved here.
        </p>
      </div>

      {isLoading && <p>Loading settings…</p>}
      {isError && <p className="form-error">Couldn't load integration settings.</p>}

      {data && (
        <div className="settings-grid">
          {INTEGRATIONS.map((spec) => (
            <IntegrationSection key={spec.key} spec={spec} settings={data} />
          ))}
        </div>
      )}
    </div>
  );
}
