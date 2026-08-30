export interface AMapOverlay {
  setMap(map: AMapMap | null): void;
}

export interface AMapMap {
  add(overlays: AMapOverlay | AMapOverlay[]): void;
  addControl(control: unknown): void;
  setFitView(
    overlays?: AMapOverlay[],
    immediately?: boolean,
    avoid?: [number, number, number, number],
  ): void;
  destroy(): void;
}

export interface AMapPolylineOptions {
  path: [number, number][];
  strokeColor: string;
  strokeWeight: number;
  strokeOpacity: number;
  lineJoin: string;
  lineCap: string;
  zIndex?: number;
}

export interface AMapMarkerOptions {
  position: [number, number];
  content: string;
  offset?: AMapPixel;
  zIndex?: number;
}

export interface AMapPixel {
  // The constructor is exposed by the AMap JS API. The interface is kept
  // intentionally small because the app only needs marker alignment.
}

export interface AMapNamespace {
  Map: new (container: HTMLElement, options?: Record<string, unknown>) => AMapMap;
  Marker: new (options: AMapMarkerOptions) => AMapOverlay;
  Pixel: new (x: number, y: number) => AMapPixel;
  Polyline: new (options: AMapPolylineOptions) => AMapOverlay;
  Scale: new () => unknown;
  ToolBar: new () => unknown;
}

interface AMapLoader {
  load(options: {
    key: string;
    version: "2.0";
    plugins: string[];
  }): Promise<AMapNamespace>;
}

declare global {
  interface Window {
    AMap?: AMapNamespace;
    AMapLoader?: AMapLoader;
    _AMapSecurityConfig?: {
      securityJsCode?: string;
    };
  }
}

const LOADER_SCRIPT_ID = "amap-jsapi-loader";
const LOADER_SCRIPT_URL = "https://webapi.amap.com/loader.js";

let loaderScriptPromise: Promise<void> | null = null;
let amapPromise: Promise<AMapNamespace> | null = null;
let amapPromiseKey = "";

function loadLoaderScript(): Promise<void> {
  if (window.AMapLoader) return Promise.resolve();
  if (loaderScriptPromise) return loaderScriptPromise;

  loaderScriptPromise = new Promise<void>((resolve, reject) => {
    const existing = document.getElementById(LOADER_SCRIPT_ID) as HTMLScriptElement | null;
    if (existing) {
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener(
        "error",
        () => reject(new Error("高德地图加载器加载失败")),
        { once: true },
      );
      return;
    }

    const script = document.createElement("script");
    script.id = LOADER_SCRIPT_ID;
    script.src = LOADER_SCRIPT_URL;
    script.async = true;
    script.onload = () => {
      if (window.AMapLoader) resolve();
      else reject(new Error("高德地图加载器未初始化"));
    };
    script.onerror = () => reject(new Error("高德地图加载器加载失败"));
    document.head.appendChild(script);
  }).catch((error) => {
    loaderScriptPromise = null;
    throw error;
  });

  return loaderScriptPromise;
}

/** Load AMap JS API 2.0 once, keeping the browser key in frontend config. */
export async function loadAMap(
  key: string,
  securityJsCode = "",
): Promise<AMapNamespace> {
  const normalizedKey = key.trim();
  if (!normalizedKey) throw new Error("未配置高德 Web 端 JS API Key");

  if (window.AMap?.Map && amapPromiseKey === normalizedKey) {
    return window.AMap;
  }
  if (amapPromise && amapPromiseKey === normalizedKey) return amapPromise;

  amapPromiseKey = normalizedKey;
  if (securityJsCode.trim()) {
    window._AMapSecurityConfig = {
      securityJsCode: securityJsCode.trim(),
    };
  }

  amapPromise = loadLoaderScript().then(() => {
    const loader = window.AMapLoader;
    if (!loader) throw new Error("高德地图加载器未初始化");
    return loader.load({
      key: normalizedKey,
      version: "2.0",
      plugins: ["AMap.Scale", "AMap.ToolBar"],
    });
  });

  try {
    return await amapPromise;
  } catch (error) {
    amapPromise = null;
    amapPromiseKey = "";
    throw error;
  }
}
