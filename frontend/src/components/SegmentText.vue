<script setup lang="ts">
import { computed } from "vue";
import type { AnswerSegment, SourceInfo } from "../types/api";
import { renderInlineMarkdown, safeExternalUrl } from "../utils/markdown";
import { usePlanStore } from "../stores/plan";

const props = defineProps<{
  segments: AnswerSegment[];
  sources: SourceInfo[];
}>();

const plan = usePlanStore();

/**
 * Only web_search sources from this response may back a sentence citation.
 * Map/weather/rail/flight evidences must not show up as web citation icons.
 */
const validWebSourceIds = computed(() => {
  const ids = new Set<string>();
  for (const s of props.sources) {
    if (
      s.source_type === "web_search" &&
      s.evidence_id &&
      safeExternalUrl(s.url)
    ) {
      ids.add(s.evidence_id);
    }
  }
  return ids;
});

interface RenderedSegment {
  html: string;
  citeIds: string[];
}

const rendered = computed<RenderedSegment[]>(() =>
  props.segments.map((seg) => ({
    html: renderInlineMarkdown(seg.text),
    citeIds: (seg.source_ids ?? []).filter((id) => validWebSourceIds.value.has(id)),
  })),
);

function openSegmentSources(ids: string[]) {
  plan.setMessageSources(props.sources);
  plan.highlightSources(ids);
}
</script>

<template>
  <p class="segment-paragraph">
    <template v-for="(seg, i) in rendered" :key="i">
      <!-- eslint-disable-next-line vue/no-v-html -->
      <span v-html="seg.html"></span>
      <button
        v-if="seg.citeIds.length"
        class="citation-badge"
        type="button"
        :title="`查看本句的 ${seg.citeIds.length} 个联网来源`"
        @click="openSegmentSources(seg.citeIds)"
      >
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
          <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
          <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
        </svg>
        {{ seg.citeIds.length }}
      </button>
    </template>
  </p>
</template>

<style scoped>
.segment-paragraph {
  margin: 0;
  white-space: pre-wrap;
}
</style>
