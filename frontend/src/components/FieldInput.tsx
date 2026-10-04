import type { FieldSpec } from "../services/api";

interface Props {
  field: FieldSpec;
  value: string;
  onChange: (value: string) => void;
  idPrefix: string;
  // For secret fields: masked version of the saved value, e.g. "••••••••1234".
  savedMask?: string;
}

export default function FieldInput({ field, value, onChange, idPrefix, savedMask }: Props) {
  const id = `${idPrefix}-${field.key}`;
  const help = field.help ? <p className="help" id={`${id}-help`}>{field.help}</p> : null;
  const describedBy = field.help ? `${id}-help` : undefined;

  if (field.kind === "checkbox") {
    return (
      <div className="field">
        <label className="checkbox-row" htmlFor={id}>
          <input
            id={id}
            type="checkbox"
            checked={value === "true"}
            onChange={(e) => onChange(e.target.checked ? "true" : "false")}
            aria-describedby={describedBy}
          />
          {field.label}
        </label>
        {help}
      </div>
    );
  }

  let input;
  if (field.kind === "textarea") {
    input = (
      <textarea id={id} value={value} placeholder={field.placeholder} rows={3}
        onChange={(e) => onChange(e.target.value)} aria-describedby={describedBy} />
    );
  } else if (field.kind === "select") {
    input = (
      <select id={id} value={value || field.default} onChange={(e) => onChange(e.target.value)}
        aria-describedby={describedBy}>
        {field.options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    );
  } else {
    const secret = field.kind === "secret";
    input = (
      <input
        id={id}
        type={secret ? "password" : "text"}
        value={value}
        autoComplete="off"
        spellCheck={false}
        placeholder={secret && savedMask ? `Saved: ${savedMask} (leave empty to keep)` : field.placeholder}
        onChange={(e) => onChange(e.target.value)}
        aria-describedby={describedBy}
      />
    );
  }

  return (
    <div className="field">
      <label htmlFor={id}>
        {field.label}
        {field.required ? "" : <span className="muted"> (optional)</span>}
      </label>
      {input}
      {help}
    </div>
  );
}
