<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import * as api from "../api";
import type { RoutePlan } from "../types/api";
import { usePlanStore } from "../stores/plan";
import { useSessionStore } from "../stores/session";
import { loadAMap, type AMapMap, type AMapOverlay } from "../utils/amap";

const props = defineProps<{ routes: RoutePlan[] }>();
const plan = usePlanStore();
const session = useSessionStore();

const amapJsKey = String(import.meta.env.VITE_AMAP_JS_KEY ?? "").trim();
const amapSecurityJsCode = String(import.meta.env.VITE_AMAP_SECURITY_JS_CODE ?? "").trim();
const interactiveConfigured = Boolean(amapJsKey);
const mapContainer = ref<HTMLElement | null>(null);
const mapUrl = ref("");
const loading = ref(false);
const error = ref("");
const interactiveVisible = ref(interactiveConfigured);
const interactiveLoading = ref(false);
const interactiveActive = ref(false);
const interactiveError = ref("");
let interactiveMap: AMapMap | null = null;
let interactiveOverlays: AMapOverlay[] = [];
let requestId = 0;

const visibleRoutes = computed(() =>
  props.routes.filter((route) => route.polyline?.length >= 2).slice(0, 6),
);

const mapAlt = computed(() => {
  const names = visibleRoutes.value
    .slice(0, 3)
    .map((route) => `${route.origin}到${route.destination}`)
    .join("、");
  return names ? `高德路线预览：${names}` : "高德路线预览";
});

const mapProviderLabel = computed(() =>
  interactiveActive.value ? "可缩放拖拽" : "高德静态图",
);

const mapNote = computed(() => {
  const prefix = interactiveError.value
    ? "交互地图加载失败，已切换静态图；"
    : !interactiveConfigured && !interactiveActive.value
      ? "当前为静态地图；配置 Web 端 JS API Key 后可缩放拖拽；"
      : "";
  return `${prefix}路线来自当前查询结果，实际出行请以现场路况为准。`;
});

const mapBounds = computed(() => {
  const points = visibleRoutes.value.flatMap((route) => route.polyline ?? []);
  const longitudes = points.map(([longitude]) => longitude);
  const latitudes = points.map(([, latitude]) => latitude);
  const minLongitude = Math.min(...longitudes);
  const maxLongitude = Math.max(...longitudes);
  const minLatitude = Math.min(...latitudes);
  const maxLatitude = Math.max(...latitudes);
  return {
    minLongitude: Number.isFinite(minLongitude) ? minLongitude : 0,
    maxLongitude: Number.isFinite(maxLongitude) ? maxLongitude : 1,
    minLatitude: Number.isFinite(minLatitude) ? minLatitude : 0,
    maxLatitude: Number.isFinite(maxLatitude) ? maxLatitude : 1,
  };
});

function projectPoint(point: [number, number]): string {
  const { minLongitude, maxLongitude, minLatitude, maxLatitude } = mapBounds.value;
  const longitudeSpan = Math.max(maxLongitude - minLongitude, 0.000001);
  const latitudeSpan = Math.max(maxLatitude - minLatitude, 0.000001);
  const x = 14 + ((point[0] - minLongitude) / longitudeSpan) * 672;
  const y = 206 - ((point[1] - minLatitude) / latitudeSpan) * 176;
  return `${x.toFixed(2)},${y.toFixed(2)}`;
}

function projectedPolyline(route: RoutePlan): string {
  return (route.polyline ?? []).map(projectPoint).join(" ");
}

function projectedEndpoint(route: RoutePlan, atEnd: boolean): string {
  const point = atEnd ? route.polyline?.[route.polyline.length - 1] : route.polyline?.[0];
  return point ? projectPoint(point) : "0,0";
}

function revokeMapUrl() {
  if (mapUrl.value.startsWith("blob:")) URL.revokeObjectURL(mapUrl.value);
  mapUrl.value = "";
}

function destroyInteractiveMap() {
  if (interactiveMap) {
    try {
      interactiveMap.destroy();
    } catch {
      // AMap may already have removed the map during a route change.
    }
  }
  interactiveMap = null;
  interactiveOverlays = [];
}

function markerContent(label: string, isEnd: boolean): string {
  return `<span class="route-map-pin${isEnd ? " is-end" : ""}">${label}</span>`;
}

async function initializeInteractiveMap(id: number): Promise<boolean> {
  if (!interactiveConfigured) return false;

  try {
    await nextTick();
    if (id !== requestId || !mapContainer.value) return false;

    const AMap = await loadAMap(amapJsKey, amapSecurityJsCode);
    if (id !== requestId || !mapContainer.value) return false;

    destroyInteractiveMap();
    const map = new AMap.Map(mapContainer.value, {
      resizeEnable: true,
      viewMode: "2D",
      zoom: 11,
    });
    const overlays: AMapOverlay[] = [];
    const routeColors = ["#1e7cf0", "#c45f3c", "#4d7896", "#9a6a0a", "#6d668f", "#4f7b59"];

    visibleRoutes.value.forEach((route, index) => {
      const path = route.polyline;
      if (path.length < 2) return;
      const color = routeColors[index % routeColors.length];
      overlays.push(
        new AMap.Polyline({
          path,
          strokeColor: color,
          strokeWeight: 5,
          strokeOpacity: 0.9,
          lineJoin: "round",
          lineCap: "round",
          zIndex: 20,
        }),
      );

      const start = path[0];
      const end = path[path.length - 1];
      const label = String(index + 1);
      overlays.push(
        new AMap.Marker({
          position: start,
          content: markerContent(label, false),
          offset: new AMap.Pixel(-13, -13),
          zIndex: 30,
        }),
        new AMap.Marker({
          position: end,
          content: markerContent(label, true),
          offset: new AMap.Pixel(-13, -13),
          zIndex: 31,
        }),
      );
    });

    if (!overlays.length) {
      map.destroy();
      return false;
    }

    map.add(overlays);
    map.addControl(new AMap.Scale());
    map.addControl(new AMap.ToolBar());
    map.setFitView(overlays, false, [42, 42, 42, 42]);
    interactiveMap = map;
    interactiveOverlays = overlays;
    interactiveActive.value = true;
    return true;
  } catch {
    if (id === requestId) {
      interactiveError.value = "交互地图暂时不可用，已切换为静态地图。";
    }
    return false;
  }
}

async function loadStaticMap(id: number) {
  if (!session.threadId || !visibleRoutes.value.length) return;

  loading.value = true;
  const currentThread = session.threadId;
  const currentVersion = plan.displayPlan?.version ?? null;
  try {
    const response = await api.getTravelPlanMap(currentThread, currentVersion);
    if (id !== requestId || session.threadId !== currentThread) return;
    mapUrl.value = URL.createObjectURL(response);
  } catch {
    if (id !== requestId || session.threadId !== currentThread) return;
    error.value = "路线地图暂时无法加载，仍可查看下方路线摘要。";
  } finally {
    if (id === requestId) {
      loading.value = false;
    }
  }
}

async function loadMap() {
  const id = ++requestId;
  revokeMapUrl();
  destroyInteractiveMap();
  error.value = "";
  interactiveError.value = "";
  interactiveActive.value = false;
  interactiveVisible.value = interactiveConfigured;
  interactiveLoading.value = false;
  loading.value = false;
  if (!session.threadId || !visibleRoutes.value.length) {
    interactiveVisible.value = false;
    return;
  }

  if (interactiveConfigured) {
    interactiveLoading.value = true;
    const ready = await initializeInteractiveMap(id);
    if (id !== requestId) return;
    interactiveLoading.value = false;
    if (ready) return;
    interactiveVisible.value = false;
  }

  await loadStaticMap(id);
}

watch(
  () => [
    session.threadId,
    plan.displayPlan?.plan_id ?? "",
    plan.displayPlan?.version ?? 0,
    visibleRoutes.value.map((route) => `${route.origin_location}|${route.destination_location}|${route.polyline.length}`).join(";")
  ],
  () => void loadMap(),
  { immediate: true },
);

onBeforeUnmount(() => {
  requestId += 1;
  destroyInteractiveMap();
  revokeMapUrl();
});
</script>

<template>
  <section class="route-map-card" aria-labelledby="route-map-title">
    <div class="route-map-heading">
      <div>
        <p class="route-map-kicker">路线预览</p>
        <h3 id="route-map-title">把行程连成一条线</h3>
      </div>
      <span class="route-map-provider">{{ mapProviderLabel }}</span>
    </div>

    <div
      v-if="interactiveVisible"
      class="route-map-interactive"
      role="application"
      :aria-label="mapAlt"
    >
      <div ref="mapContainer" class="route-map-canvas"></div>
      <div v-if="interactiveLoading" class="route-map-interactive-loading" aria-live="polite">
        <span class="spinner"></span>
        <span>正在加载可交互地图</span>
      </div>
      <div v-if="interactiveActive" class="route-map-interactive-hint" aria-hidden="true">
        滚轮缩放 · 拖动浏览
      </div>
    </div>
    <div v-else-if="loading" class="route-map-placeholder" aria-live="polite">
      <span class="spinner"></span>
      <span>正在绘制路线</span>
    </div>
    <img v-else-if="mapUrl" class="route-map-image" :src="mapUrl" :alt="mapAlt" />
    <div v-else class="route-map-fallback" role="img" :aria-label="mapAlt">
      <svg viewBox="0 0 700 220" aria-hidden="true">
        <defs>
          <pattern id="route-map-grid" width="34" height="34" patternUnits="userSpaceOnUse">
            <path d="M 34 0 L 0 0 0 34" fill="none" stroke="rgba(47, 92, 91, 0.13)" stroke-width="1" />
          </pattern>
          <linearGradient id="route-map-wash" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stop-color="#e6f0e5" />
            <stop offset="1" stop-color="#f2eadc" />
          </linearGradient>
        </defs>
        <rect width="700" height="220" fill="url(#route-map-wash)" />
        <rect width="700" height="220" fill="url(#route-map-grid)" />
        <path d="M-20 176 C 120 118, 190 196, 312 145 S 540 82, 730 122" fill="none" stroke="rgba(255,255,255,.84)" stroke-width="18" />
        <path d="M-20 176 C 120 118, 190 196, 312 145 S 540 82, 730 122" fill="none" stroke="rgba(90, 132, 124, .18)" stroke-width="2" />
        <polyline
          v-for="(route, index) in visibleRoutes"
          :key="`line-${index}`"
          :points="projectedPolyline(route)"
          fill="none"
          stroke="#0e5f54"
          stroke-width="6"
          stroke-linecap="round"
          stroke-linejoin="round"
          :opacity="Math.max(0.42, 1 - index * 0.1)"
        />
        <template v-for="(route, index) in visibleRoutes" :key="`markers-${index}`">
          <circle :cx="projectedEndpoint(route, false).split(',')[0]" :cy="projectedEndpoint(route, false).split(',')[1]" r="8" fill="#f3b568" stroke="#fffaf0" stroke-width="3" />
          <circle :cx="projectedEndpoint(route, true).split(',')[0]" :cy="projectedEndpoint(route, true).split(',')[1]" r="8" fill="#c45f3c" stroke="#fffaf0" stroke-width="3" />
        </template>
      </svg>
      <span class="route-map-fallback-label">路线示意图 · 已根据高德路径还原</span>
    </div>

    <div class="route-map-legend">
      <div v-for="(route, index) in visibleRoutes" :key="`${route.origin}-${route.destination}-${index}`" class="route-map-legend-item">
        <span class="route-map-marker">{{ String.fromCharCode(65 + index) }}</span>
        <span>{{ route.origin }} → {{ route.destination }}</span>
        <span class="route-map-distance">{{ route.distance || route.duration || "路线已获取" }}</span>
      </div>
    </div>
    <p class="route-map-note">{{ error ? "当前展示路线示意图；" : mapNote }}</p>
  </section>
</template>
