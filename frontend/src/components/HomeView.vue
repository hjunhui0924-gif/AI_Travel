<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import beihaiPhoto from "../assets/destinations/beihai-park-white-pagoda.jpg";
import forbiddenCityPhoto from "../assets/destinations/beijing-forbidden-city.jpg";
import greatWallPhoto from "../assets/destinations/beijing-great-wall-ridge.jpg";
import hangzhouPhoto from "../assets/destinations/hangzhou-west-lake-no-people.jpg";
import xiamenPhoto from "../assets/destinations/xiamen-gulangyu.jpg";
import zhangjiajiePhoto from "../assets/destinations/zhangjiajie-forest.jpg";
import guilinPhoto from "../assets/destinations/guilin-li-river.jpg";

type Destination = {
  id: string;
  name: string;
  region: string;
  landmark: string;
  category: string;
  meta: string;
  tagline: string;
  description: string;
  image: string;
};

const props = withDefaults(
  defineProps<{ embedded?: boolean; transitioning?: boolean }>(),
  { embedded: false, transitioning: false },
);

const emit = defineEmits<{
  "submit-prompt": [prompt: string];
  "background-change": [image: string];
}>();

const filters = ["全部灵感", "城市漫游", "自然风光"];
const activeFilter = ref(filters[0]);
const travelPrompt = ref("");
const activeDestinationId = ref("beihai-park");
const showBackToTop = ref(false);
let scrollRoot: HTMLElement | null = null;
const HOVER_INTENT_DELAY = 280;
let hoverTimer: number | null = null;
let hoverTargetId = "";

const destinations: Destination[] = [
  {
    id: "beihai-park",
    name: "北海公园",
    region: "北京 · 皇家园林",
    landmark: "白塔 / 琼华岛",
    category: "城市漫游",
    meta: "半日 · 湖园漫步",
    tagline: "一座白塔，把京城的时间放慢。",
    description: "沿着北海水岸看白塔倒影，再穿过静谧的园林和古建，把城市里的半天交给湖风。",
    image: beihaiPhoto,
  },
  {
    id: "hangzhou",
    name: "杭州",
    region: "浙江 · 江南水岸",
    landmark: "西湖 / 灵隐寺",
    category: "城市漫游",
    meta: "2–4 天 · 松弛散步",
    tagline: "给自己一段被湖水放慢的时间。",
    description: "沿湖散步、看一场日落，再钻进茶山和老街，让每一站都留一点不赶路的余地。",
    image: hangzhouPhoto,
  },
  {
    id: "xiamen",
    name: "厦门",
    region: "福建 · 岛屿日常",
    landmark: "鼓浪屿 / 环岛路",
    category: "城市漫游",
    meta: "2–3 天 · 海边放空",
    tagline: "海风会替你把周末吹松。",
    description: "旧别墅、海岸线和一杯冰咖啡，给忙碌的日子留一个轻轻转身的出口。",
    image: xiamenPhoto,
  },
  {
    id: "zhangjiajie",
    name: "张家界",
    region: "湖南 · 峰林之间",
    landmark: "国家森林公园",
    category: "自然风光",
    meta: "3–5 天 · 山野呼吸",
    tagline: "把城市的噪音，留在山脚下。",
    description: "穿行在云雾和峰柱之间，路线不必塞满，给每一次停下来拍照的心情留出时间。",
    image: zhangjiajiePhoto,
  },
  {
    id: "guilin",
    name: "桂林",
    region: "广西 · 漓江山水",
    landmark: "漓江 / 阳朔",
    category: "自然风光",
    meta: "3–4 天 · 山水慢游",
    tagline: "坐在一叶竹筏上，看山水自己经过。",
    description: "把一天交给漓江，把傍晚交给阳朔的街巷，风景会在慢下来以后变得更清楚。",
    image: guilinPhoto,
  },
  {
    id: "mutianyu",
    name: "慕田峪",
    region: "北京 · 长城北线",
    landmark: "慕田峪长城",
    category: "自然风光",
    meta: "1–2 天 · 近郊徒步",
    tagline: "沿着城墙走，把风景看得更远。",
    description: "避开拥挤的时间，沿着山脊走一段长城，让城市周末也能拥有一场真正的远行。",
    image: greatWallPhoto,
  },
  {
    id: "forbidden-city",
    name: "北京 · 角楼",
    region: "北京 · 城墙水岸",
    landmark: "故宫角楼",
    category: "城市漫游",
    meta: "半日 · 摄影散步",
    tagline: "一场晚霞，足够让古城重新发光。",
    description: "在护城河边等一束夕阳，看看屋檐、倒影和人群如何一起收进黄昏。",
    image: forbiddenCityPhoto,
  },
];

const visibleDestinations = computed(() => {
  if (activeFilter.value === "全部灵感") return destinations;
  return destinations.filter((destination) => destination.category === activeFilter.value);
});

const activeDestination = computed(
  () => destinations.find((destination) => destination.id === activeDestinationId.value) ?? destinations[0],
);

function submitPrompt() {
  const prompt = travelPrompt.value.trim();
  emit("submit-prompt", prompt || "帮我设计一段适合现在出发的旅行");
}

function clearHoverIntent() {
  if (hoverTimer !== null) {
    window.clearTimeout(hoverTimer);
    hoverTimer = null;
  }
  hoverTargetId = "";
}

function activateDestination(destination: Destination) {
  clearHoverIntent();
  if (activeDestinationId.value !== destination.id) activeDestinationId.value = destination.id;
}

function queueDestination(destination: Destination) {
  if (activeDestinationId.value === destination.id) {
    clearHoverIntent();
    return;
  }
  clearHoverIntent();
  hoverTargetId = destination.id;
  hoverTimer = window.setTimeout(() => {
    if (hoverTargetId === destination.id) activateDestination(destination);
  }, HOVER_INTENT_DELAY);
}

function cancelQueuedDestination(destinationId: string) {
  if (hoverTargetId === destinationId) clearHoverIntent();
}

function scrollToTop() {
  if (scrollRoot) scrollRoot.scrollTo({ top: 0, behavior: "smooth" });
  else window.scrollTo({ top: 0, behavior: "smooth" });
}

function scrollToNextScene() {
  const firstScene = scrollRoot?.querySelector<HTMLElement>(".home-destination-scene");
  if (firstScene && scrollRoot) scrollRoot.scrollTo({ top: firstScene.offsetTop - 20, behavior: "smooth" });
}

function onScroll() {
  showBackToTop.value = Boolean(scrollRoot && scrollRoot.scrollTop > 240);
}

watch(activeDestination, (destination) => emit("background-change", destination.image), { immediate: true });

onMounted(() => {
  scrollRoot = props.embedded ? document.querySelector<HTMLElement>(".chat-scroll") : null;
  scrollRoot?.addEventListener("scroll", onScroll, { passive: true });
});

onBeforeUnmount(() => {
  clearHoverIntent();
  scrollRoot?.removeEventListener("scroll", onScroll);
});
</script>

<template>
  <main class="home-view" :class="{ 'is-embedded': props.embedded, 'is-transitioning': props.transitioning }">
    <section class="home-hero">
      <div class="home-hero-topline">
        <span class="home-product-name">智能旅行规划</span>
        <span class="home-live-status"><i></i> 灵感正在发生</span>
      </div>

      <div class="home-hero-content">
        <div class="home-hero-copy">
          <p class="home-eyebrow"><span class="home-eyebrow-line"></span> 旅行规划 · 现在出发</p>
          <h1>世界等你探索，<br /><em>关于旅行的一切，都可以问我。</em></h1>
          <p class="home-hero-description">
            告诉我目的地、时间和心情。我会把一段模糊的想法，整理成真正适合你的旅行。
          </p>

          <form class="home-prompt" @submit.prevent="submitPrompt">
            <svg class="home-prompt-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="10.8" cy="10.8" r="6.8" /><path d="m16 16 4 4" /></svg>
            <label class="sr-only" for="travel-prompt">告诉旅行规划助手你想去哪里</label>
            <input id="travel-prompt" v-model="travelPrompt" type="text" autocomplete="off" aria-label="告诉我你的旅行计划" placeholder="想去哪？告诉我你的旅行计划" :disabled="props.transitioning" />
            <button type="submit" aria-label="发送旅行需求" :disabled="props.transitioning">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13" /><path d="m13 6 6 6-6 6" /></svg>
            </button>
          </form>

          <div class="home-quick-prompts" aria-label="快捷灵感">
            <span>可以这样开始</span>
            <button type="button" @click="travelPrompt = '周末去海边，想慢一点'">周末去海边</button>
            <button type="button" @click="travelPrompt = '带爸妈去北京玩 4 天'">带爸妈去北京</button>
            <button type="button" @click="travelPrompt = '想看山，也想吃当地美食'">看山 · 吃当地</button>
          </div>
        </div>
      </div>

      <button class="home-scroll-hint" type="button" aria-label="向下查看景点" @click="scrollToNextScene">
        <span>向下探索景点</span>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v13" /><path d="m6 13 6 6 6-6" /></svg>
      </button>

      <div class="home-hero-current" aria-live="polite">
        <span class="home-hero-current-label">正在浏览</span>
        <strong>{{ activeDestination.name }}</strong>
        <span>{{ activeDestination.landmark }}</span>
        <span class="home-hero-current-index">{{ String(destinations.findIndex((item) => item.id === activeDestination.id) + 1).padStart(2, "0") }} / {{ String(destinations.length).padStart(2, "0") }}</span>
      </div>
    </section>

    <section class="home-destination-feed home-section" aria-labelledby="destination-title">
      <div class="home-feed-heading">
        <div>
          <p class="home-section-kicker">01 / 目的地灵感</p>
          <h2 id="destination-title">走进一处风景</h2>
        </div>
        <p class="home-section-note">真实目的地，<br />从第一眼开始。</p>
      </div>

      <div class="home-filter-row" role="tablist" aria-label="目的地类型">
        <button
          v-for="filter in filters"
          :key="filter"
          type="button"
          role="tab"
          :aria-selected="activeFilter === filter"
          :class="{ active: activeFilter === filter }"
          @click="activeFilter = filter"
        >
          {{ filter }}
        </button>
        <span class="home-filter-rule"></span>
        <span class="home-filter-count">{{ visibleDestinations.length }} 处</span>
      </div>

      <div class="home-destination-list">
        <article
          v-for="(destination, index) in visibleDestinations"
          :key="destination.id"
          class="home-destination-scene"
          :data-destination-id="destination.id"
          tabindex="0"
          @mouseenter="queueDestination(destination)"
          @mouseleave="cancelQueuedDestination(destination.id)"
          @focusin="activateDestination(destination)"
        >
          <img :src="destination.image" :alt="`${destination.name} · ${destination.landmark}`" :loading="index === 0 ? 'eager' : 'lazy'" />
          <div class="home-scene-shade" aria-hidden="true"></div>
          <div class="home-scene-topline"><span>{{ String(index + 1).padStart(2, "0") }}</span><span>{{ destination.category }}</span></div>
          <div class="home-scene-copy">
            <p class="home-scene-region">{{ destination.region }} · {{ destination.landmark }}</p>
            <h3>{{ destination.name }}</h3>
            <p class="home-scene-tagline">{{ destination.tagline }}</p>
            <p class="home-scene-description">{{ destination.description }}</p>
            <div class="home-scene-meta"><span>{{ destination.meta }}</span><span class="home-scene-line"></span><span>旅行灵感</span></div>
          </div>
        </article>
      </div>
    </section>

    <section class="home-endcard home-section">
      <div>
        <p class="home-section-kicker">02 / 定制你的旅程</p>
        <h2>下一站，<br /><em>由你开始。</em></h2>
      </div>
      <button class="home-endcard-cta" type="button" @click="submitPrompt"><span>开始设计行程</span><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13" /><path d="m13 6 6 6-6 6" /></svg></button>
    </section>

    <button v-if="showBackToTop" class="home-back-to-top" type="button" aria-label="回到顶部" title="回到顶部" @click="scrollToTop">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5" /><path d="m6 11 6-6 6 6" /></svg>
    </button>
  </main>
</template>
