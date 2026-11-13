import { useRef, useState, type ReactNode } from "react";
import { Check, Copy, ImageDown, Maximize2, Minimize2, ChevronDown, ChevronUp } from "lucide-react";

function safeName(title: string) {
  return title.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "-").trim() || "visualization";
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

async function svgPngBlob(svg: SVGSVGElement) {
  const clone = svg.cloneNode(true) as SVGSVGElement;
  const width = Math.ceil(svg.clientWidth || svg.viewBox.baseVal.width || 1200);
  const height = Math.ceil(svg.clientHeight || svg.viewBox.baseVal.height || 800);
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  clone.setAttribute("width", String(width));
  clone.setAttribute("height", String(height));
  const image = new Image();
  const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(clone)], { type: "image/svg+xml;charset=utf-8" }));
  try {
    await new Promise<void>((resolve, reject) => { image.onload = () => resolve(); image.onerror = () => reject(new Error("Image conversion failed")); image.src = url; });
    const scale = 2;
    const canvas = document.createElement("canvas");
    canvas.width = width * scale;
    canvas.height = height * scale;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Cannot create image canvas");
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    return await new Promise<Blob>((resolve, reject) => canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error("Image conversion failed")), "image/png"));
  } finally { URL.revokeObjectURL(url); }
}

async function pngBlob(container: HTMLElement) {
  const mermaidSvg = container.querySelector(".mermaid-diagram svg") as SVGSVGElement | null;
  // Mermaid already outputs a self-contained SVG. Its foreignObject nodes can
  // make DOM capture fail, so rasterize it directly before using the DOM path.
  if (mermaidSvg) return svgPngBlob(mermaidSvg);
  const { toBlob } = await import("html-to-image");
  const blob = await toBlob(container, {
    backgroundColor: "#ffffff",
    pixelRatio: 2,
    cacheBust: true,
    filter: (node) => !(node instanceof HTMLElement && node.classList.contains("visualization-frame__actions")),
  });
  if (!blob) throw new Error("Image conversion failed");
  return blob;
}

export function VisualizationFrame({ title, source, children }: { title: string; source: string; children: ReactNode }) {
  const contentRef = useRef<HTMLDivElement>(null);
  const [showSource, setShowSource] = useState(false);
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [exportError, setExportError] = useState(false);
  const flashCopied = () => { setCopied(true); window.setTimeout(() => setCopied(false), 1500); };
  const downloadPng = async () => {
    if (!contentRef.current) return;
    try { saveBlob(await pngBlob(contentRef.current), `${safeName(title)}.png`); setExportError(false); } catch { setExportError(true); }
  };
  const copyImage = async () => {
    if (!contentRef.current || !navigator.clipboard?.write || typeof ClipboardItem === "undefined") return;
    try { await navigator.clipboard.write([new ClipboardItem({ "image/png": await pngBlob(contentRef.current) })]); flashCopied(); setExportError(false); } catch { setExportError(true); }
  };
  return <section className={`visualization-frame${expanded ? " visualization-frame--expanded" : ""}`} aria-label={title}>
    <header className="visualization-frame__header">
      <span>{title}{exportError && <em className="visualization-frame__export-error">Image export failed</em>}</span>
      <span className="visualization-frame__actions">
        <button onClick={copyImage} title="Copy image" aria-label="Copy image">{copied ? <Check size={13} /> : <Copy size={13} />}</button>
        <button onClick={downloadPng} title="Download PNG" aria-label="Download PNG"><ImageDown size={14} /></button>
        <button onClick={() => setExpanded((value) => !value)} title={expanded ? "Exit fullscreen" : "Zoom in"} aria-label={expanded ? "Exit fullscreen" : "Zoom in"}>{expanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}</button>
        <button onClick={() => setShowSource((value) => !value)} title={showSource ? "Hide source data" : "Show source data"} aria-label={showSource ? "Hide source data" : "Show source data"}>{showSource ? <ChevronUp size={14} /> : <ChevronDown size={14} />}</button>
      </span>
    </header>
    <div ref={contentRef} className="visualization-frame__content">{children}</div>
    {showSource && <pre className="visualization-frame__source">{source}</pre>}
  </section>;
}
