import { useRef, useState } from "react";
import { api, type ImageInfo } from "../services/api";

export interface AttachedImage {
  info: ImageInfo;
  previewUrl: string;
  alt: string;
}

interface Props {
  images: AttachedImage[];
  onChange: (images: AttachedImage[]) => void;
  disabled?: boolean;
}

function size(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function ImagePicker({ images, onChange, disabled }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function addFiles(files: FileList | null) {
    if (!files?.length) return;
    setUploading(true);
    setError(null);
    const added: AttachedImage[] = [];
    const errors: string[] = [];
    for (const file of Array.from(files)) {
      try {
        const info = await api.uploadImage(file);
        added.push({ info, previewUrl: URL.createObjectURL(file), alt: "" });
      } catch (err) {
        errors.push(err instanceof Error ? err.message : String(err));
      }
    }
    onChange([...images, ...added]);
    if (errors.length) setError(errors.join(" "));
    setUploading(false);
    if (inputRef.current) inputRef.current.value = "";
  }

  function remove(id: string) {
    const image = images.find((i) => i.info.id === id);
    if (image) URL.revokeObjectURL(image.previewUrl);
    onChange(images.filter((i) => i.info.id !== id));
    api.deleteImage(id).catch(() => undefined);
  }

  function setAlt(id: string, alt: string) {
    onChange(images.map((i) => (i.info.id === id ? { ...i, alt } : i)));
  }

  return (
    <div>
      <div className="row">
        <button type="button" onClick={() => inputRef.current?.click()} disabled={disabled || uploading}>
          {uploading ? "Adding…" : "🖼 Add images"}
        </button>
        <span className="help">JPEG, PNG, WEBP or GIF. Each platform's limits are checked below.</span>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp,image/gif"
        multiple
        hidden
        onChange={(e) => addFiles(e.target.files)}
      />
      {error && <div className="notice bad" role="alert"><p>{error}</p></div>}
      {images.length > 0 && (
        <div className="images">
          {images.map((img, index) => (
            <div className="image-tile" key={img.info.id}>
              <img src={img.previewUrl} alt={img.alt || `Attached image ${index + 1}`} />
              <div className="meta">
                <div className="row">
                  <span className="spacer" title={img.info.filename}>
                    {img.info.format} · {img.info.width}×{img.info.height} · {size(img.info.size_bytes)}
                  </span>
                  <button type="button" className="link-button" onClick={() => remove(img.info.id)}
                    disabled={disabled} aria-label={`Remove ${img.info.filename}`}>
                    Remove
                  </button>
                </div>
                <label className="sr-only" htmlFor={`alt-${img.info.id}`}>Image description</label>
                <input
                  id={`alt-${img.info.id}`}
                  type="text"
                  placeholder="Description (alt text)"
                  value={img.alt}
                  disabled={disabled}
                  onChange={(e) => setAlt(img.info.id, e.target.value)}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
