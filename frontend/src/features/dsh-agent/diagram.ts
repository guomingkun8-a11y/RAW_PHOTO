export const MAX_DIAGRAM_LENGTH = 20000;

const diagramStart = /^(?:flowchart|graph|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|mindmap|timeline|gantt|pie|journey|gitGraph|quadrantChart|requirementDiagram|xychart(?:-beta)?|block(?:-beta)?|sankey-beta|packet(?:-beta)?|architecture-beta|kanban|treemap(?:-beta)?|C4Context)\b/;

export function isDiagramFence(language: string, code: string) {
  const name = language.trim().split(/\s+/)[0]?.toLowerCase() || "";
  return name === "mermaid" || (["", "mindmap", "flowchart"].includes(name) && diagramStart.test(code.trimStart()));
}

export function diagramLabel(code: string) {
  const type = code.trimStart().split(/\s/)[0];
  return type === "mindmap" ? "思维导图" : type === "sequenceDiagram" ? "时序图" : ["flowchart", "graph"].includes(type || "") ? "流程图" : "图表";
}

let pending = Promise.resolve();
let sequence = 0;

function diagramPalette() {
  const styles = getComputedStyle(document.documentElement);
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1;
  const context = canvas.getContext("2d")!;
  const background = styles.getPropertyValue("--studio-panel").trim() || "#ffffff";
  function color(variable: string) {
    context.fillStyle = background;
    context.fillRect(0, 0, 1, 1);
    context.fillStyle = styles.getPropertyValue(variable).trim() || "#334155";
    context.fillRect(0, 0, 1, 1);
    return "#" + Array.from(context.getImageData(0, 0, 1, 1).data).slice(0, 3).map(value => value.toString(16).padStart(2, "0")).join("");
  }
  return { background: color("--studio-panel"), foreground: color("--foreground"), muted: color("--muted-foreground"), accent: color("--accent"), border: color("--input") };
}

export function renderDiagram(code: string, signal: AbortSignal): Promise<string> {
  const result = pending.then(async () => {
    signal.throwIfAborted();
    if (code.length > MAX_DIAGRAM_LENGTH) throw new Error("图表内容过长，请拆分成较小的图表。");
    if (/%%\s*\{|^---\s*$/m.test(code)) throw new Error("图表不支持内嵌配置，请移除配置后重试。");
    const mermaid = (await import("mermaid")).default;
    signal.throwIfAborted();
    const palette = diagramPalette();
    const branches = document.documentElement.classList.contains("dark")
      ? ["#30456b", "#244e40", "#5a4929", "#5a344a"]
      : ["#e2ebff", "#d2eee3", "#f3e7c6", "#efdeea"];
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      suppressErrorRendering: true,
      maxTextSize: MAX_DIAGRAM_LENGTH,
      maxEdges: 150,
      theme: "base",
      fontFamily: "system-ui, sans-serif",
      htmlLabels: false,
      flowchart: { htmlLabels: false, useMaxWidth: false },
      themeVariables: {
        background: palette.background, primaryColor: palette.background, primaryTextColor: palette.foreground,
        primaryBorderColor: palette.accent, lineColor: palette.muted, textColor: palette.foreground,
        secondaryColor: palette.background, tertiaryColor: palette.background,
        mainBkg: palette.background, nodeBorder: palette.accent, clusterBkg: palette.background,
        clusterBorder: palette.border, edgeLabelBackground: palette.background,
        git0: palette.accent, gitBranchLabel0: "#ffffff",
        ...Object.fromEntries(Array.from({ length: 12 }, (_, index) => [
          [`cScale${index}`, branches[index % branches.length]],
          [`cScaleLabel${index}`, palette.foreground], [`cScaleInv${index}`, palette.muted],
        ]).flat()),
      },
      dompurifyConfig: { FORBID_TAGS: ["foreignObject", "image"], FORBID_ATTR: ["href", "xlink:href"] },
    });
    // Mermaid measures text in the DOM; isolate its scratch output from the chat.
    const scratch = document.createElement("div");
    scratch.style.cssText = "position:fixed;inset:0 auto auto 0;width:1000px;visibility:hidden;pointer-events:none";
    scratch.inert = true;
    document.body.append(scratch);
    try {
      const { svg } = await mermaid.render(`dsh-diagram-${++sequence}`, code, scratch);
      signal.throwIfAborted();
      const documentSvg = new DOMParser().parseFromString(svg, "image/svg+xml");
      if (documentSvg.querySelector("parsererror")) throw new Error("图表格式无效，暂时无法预览。");
      const root = documentSvg.documentElement;
      // Measure the final SVG, not the renderer's intermediate layout bounds.
      scratch.append(root);
      const box = (root as unknown as SVGSVGElement).getBBox();
      if (!(box.width > 0 && box.height > 0)) throw new Error("图表内容为空，暂时无法预览。");
      const width = Math.ceil(box.width + 32);
      const height = Math.ceil(box.height + 32);
      root.setAttribute("viewBox", `${box.x - 16} ${box.y - 16} ${width} ${height}`);
      root.setAttribute("width", String(width));
      root.setAttribute("height", String(height));
      root.setAttribute("xmlns", "http://www.w3.org/2000/svg");
      root.setAttribute("style", `background:${palette.background};font-family:system-ui,sans-serif`);
      return new XMLSerializer().serializeToString(root);
    } finally {
      scratch.remove();
    }
  });
  // Mermaid owns global renderer settings, so simultaneous messages must serialize.
  pending = result.then(() => undefined, () => undefined);
  return result;
}
