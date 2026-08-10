<script setup lang="ts">
import { computed } from "vue";
import { usePlanStore } from "../stores/plan";
import { safeExternalUrl } from "../utils/markdown";
import type { Evidence, SourceInfo } from "../types/api";

const plan = usePlanStore();

interface SourceCardModel {
  key: string;
  title: string;
  url: string | null; // null = non-http(s), render as plain text
  rawUrl: string;
  meta: string[];
  snippet: string;
  isDemo: boolean;
  highlighted: boolean;
}

const FRESHNESS_LABELS: Record<string, string> = {
  fresh: "新鲜",
  recent: "近期",
  stale: "可能过期",
  unknown: "时效未知",
};

const cards = computed<SourceCardModel[]>(() =>
  plan.allSources.map((s: SourceInfo | Evidence, index) => {
    const id = s.evidence_id || "";
    const meta: string[] = [];
    if (s.provider) meta.push(s.provider);
    if (s.source_type) meta.push(s.source_type);
    if (s.freshness) meta.push(FRESHNESS_LABELS[s.freshness] ?? s.freshness);
    const retrievedAt = (s as Evidence).retrieved_at || (s as SourceInfo).source_date;
    if (retrievedAt) meta.push(`查询于 ${String(retrievedAt).slice(0, 16).replace("T", " ")}`);
    return {
      key: id || s.url || s.title || `source-${index}`,
      title: s.title || "未命名来源",
      url: safeExternalUrl(s.url),
      rawUrl: s.url || "",
      meta,
      // SSE cards use `summary`; plan Evidence uses `snippet`.
      snippet: (s as SourceInfo).summary ?? (s as Evidence).snippet ?? "",
      isDemo: !!s.is_demo,
      highlighted: id ? plan.highlightedSourceIds.includes(id) : false,
    };
  }),
);
</script>

<template>
  <div v-if="!cards.length" class="panel-empty">
    <div class="empty-title">暂无来源</div>
    <p>助手回答引用或旅行计划使用的来源会展示在这里；点击聊天中句末的链条图标可定位到对应来源。</p>
  </div>
  <template v-else>
    <div
      v-for="card in cards"
      :key="card.key"
      class="source-card"
      :class="{ highlighted: card.highlighted }"
    >
      <div class="source-title">{{ card.title }}</div>
      <div class="source-meta">
        <span v-for="(m, i) in card.meta" :key="i">{{ m }}</span>
        <span v-if="card.isDemo" class="badge demo">演示数据</span>
      </div>
      <div v-if="card.snippet" class="source-snippet">{{ card.snippet }}</div>
      <a
        v-if="card.url"
        class="source-url"
        :href="card.url"
        target="_blank"
        rel="noopener noreferrer"
      >
        {{ card.url }}
      </a>
      <span v-else-if="card.rawUrl" class="source-url dead">{{ card.rawUrl }}</span>
    </div>
  </template>
</template>
