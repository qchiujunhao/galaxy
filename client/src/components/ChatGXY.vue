<script setup lang="ts">
import { faExternalLinkAlt, faMagic, faMicroscope, faPlus, faTrash } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/vue-fontawesome";
import { BAlert, BSkeleton } from "bootstrap-vue";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { GalaxyApi } from "@/api";
import { getGalaxyInstance } from "@/app";
import { useAgentActions } from "@/composables/agentActions";
import { useDataAnalysisAgent } from "@/composables/agents/dataAnalysis";
import { useMarkdown } from "@/composables/markdown";
import { useChatGxyConversation } from "@/composables/useChatGxyConversation";
import { errorMessageAsString } from "@/utils/simple-error";

import { getAgentIcon } from "./ChatGXY/agentTypes";
import type { ChatMessage } from "./ChatGXY/types";
import { escapeHtml, generateId, hasArtifacts, isAwaitingExecution, scrollToBottom } from "./ChatGXY/utilities";

import GButton from "./BaseComponents/GButton.vue";
import ChatInput from "./ChatGXY/ChatInput.vue";
import ChatMessageCell from "./ChatGXY/ChatMessageCell.vue";
import MessageArtifacts from "./ChatGXY/MessageArtifacts.vue";
import MessageIntermediateDetails from "./ChatGXY/MessageIntermediateDetails.vue";
import DatasetSelector from "./Form/Elements/FormData/FormData.vue";
import Heading from "@/components/Common/Heading.vue";
import LoadingSpan from "@/components/LoadingSpan.vue";

const props = withDefaults(
    defineProps<{
        exchangeId?: string;
        compact?: boolean;
    }>(),
    {
        exchangeId: undefined,
        compact: false,
    },
);

const query = ref("");
const messages = ref<ChatMessage[]>([]);
const busy = ref(false);
const chatContainer = ref<HTMLElement>();
const selectedAgentType = ref("auto");
const currentChatId = ref<string | null>(null);
const hasLoadedInitialChat = ref(false);

// TODO: Conditionally allow this if we have the Data Analysis agent available?
/** Whether the Data Analysis agent is currently being used */
const usingDataAnalysisAgent = ref(false);

// Data Analysis agent state and actions
const {
    appendAssistantMessage,
    closeChatStream,
    datasetError,
    datasetOptions,
    formDataOptions,
    loadingDatasets,
    pendingCollapsedMessages,
    prepareConversationReplay,
    pyodideExecutions,
    pyodideRunnerRunning,
    rebuildConversationMessages,
    resetConversationState,
    selectedDatasets,
    selectedDatasetsFormData,
} = useDataAnalysisAgent(usingDataAnalysisAgent, messages, currentChatId, selectedAgentType);

const isChatBusy = computed(
    () => busy.value || pyodideRunnerRunning.value || messages.value.some((message) => isAwaitingExecution(message)),
);

const { renderMarkdown } = useMarkdown({ openLinksInNewPage: true, removeNewlinesAfterList: true });
const { processingAction, handleAction } = useAgentActions();

onMounted(async () => {
    if (props.exchangeId) {
        await loadChatById(props.exchangeId);
    } else {
        await loadLatestChat();
    }

    if (!hasLoadedInitialChat.value) {
        showWelcome();
    }
});

onBeforeUnmount(() => {
    closeChatStream();
});

watch(
    () => props.exchangeId,
    async (newId, oldId) => {
        if (newId === oldId) {
            return;
        }
        if (newId) {
            await loadChatById(newId);
        } else {
            startNewChat();
        }
    },
);

function safeRenderMarkdown(text: string): string {
    try {
        return renderMarkdown(text);
    } catch (error) {
        console.error("Failed to render markdown for chat message:", error);
        return `<pre>${escapeHtml(text)}</pre>`;
    }
}

function showWelcome() {
    messages.value.push({
        id: generateId(),
        role: "assistant",
        content:
            "Welcome to ChatGXY. Ask about tools, workflows, errors, or data quality " +
            "and your question will be routed to the appropriate specialist agent.",
        timestamp: new Date(),
        agentType: "router",
        confidence: "high",
        feedback: null,
        isSystemMessage: true,
    });
}

const { deleteCurrentChat, loadChatById, loadLatestChat, startNewChat, syncRouteToExchange } = useChatGxyConversation({
    chatContainer,
    currentChatId,
    hasLoadedInitialChat,
    messages,
    query,
    prepareConversationReplay,
    rebuildConversationMessages,
    resetConversationState,
});

async function submitQuery() {
    if (!query.value.trim()) {
        return;
    }
    pendingCollapsedMessages.length = 0;

    const userMessage: ChatMessage = {
        id: generateId(),
        role: "user",
        content: query.value,
        timestamp: new Date(),
        feedback: null,
    };

    messages.value.push(userMessage);
    const currentQuery = query.value;
    query.value = "";

    await nextTick();
    scrollToBottom(chatContainer.value);

    busy.value = true;

    try {
        const { data, error } = await GalaxyApi().POST("/api/chat", {
            params: {
                query: {
                    agent_type: selectedAgentType.value,
                },
            },
            body: {
                query: currentQuery,
                context: null,
                exchange_id: currentChatId.value,
                agent_type: selectedAgentType.value,
                dataset_ids: usingDataAnalysisAgent.value ? selectedDatasets.value : undefined,
            },
        });

        if (error) {
            const errorText = errorMessageAsString(error, "Failed to get response from ChatGXY.");
            const errorMsg: ChatMessage = {
                id: generateId(),
                role: "assistant",
                content: `Error: ${errorText}`,
                timestamp: new Date(),
                agentType: selectedAgentType.value,
                confidence: "low",
                feedback: null,
            };
            messages.value.push(errorMsg);

            await nextTick();
            scrollToBottom(chatContainer.value);
        } else if (data) {
            if (data.exchange_id) {
                syncRouteToExchange(data.exchange_id);
            }

            appendAssistantMessage(data, selectedAgentType.value);

            await nextTick();
            scrollToBottom(chatContainer.value);
        }
    } catch (e) {
        console.error("Unexpected chat error:", e);
        const errorMsg: ChatMessage = {
            id: generateId(),
            role: "assistant",
            content: "Unexpected error occurred. Please try again.",
            timestamp: new Date(),
            agentType: selectedAgentType.value,
            confidence: "low",
            feedback: null,
        };
        messages.value.push(errorMsg);

        await nextTick();
        scrollToBottom(chatContainer.value);
    } finally {
        busy.value = false;
        await nextTick();
        scrollToBottom(chatContainer.value);
    }
}

watch(busy, (isBusy) => {
    if (isBusy) {
        nextTick(() => scrollToBottom(chatContainer.value));
    }
});

async function sendFeedback(messageId: string, value: "up" | "down") {
    const message = messages.value.find((m) => m.id === messageId);
    if (message) {
        message.feedback = value;

        if (currentChatId.value) {
            try {
                const feedbackValue = value === "up" ? 1 : 0;
                const { error } = await GalaxyApi().PUT("/api/chat/exchange/{exchange_id}/feedback", {
                    params: {
                        path: { exchange_id: currentChatId.value },
                    },
                    body: feedbackValue,
                });

                if (error) {
                    console.error("Failed to save feedback:", error);
                    message.feedback = null;
                }
            } catch (e) {
                console.error("Failed to save feedback:", e);
                message.feedback = null;
            }
        }
    }
}

function popOutToScratchbook() {
    const Galaxy = getGalaxyInstance();
    const path = currentChatId.value ? `/chatgxy/${currentChatId.value}` : "/chatgxy";
    const url = `${path}?compact=true`;
    Galaxy.frame.add({ title: "ChatGXY", url });
}
</script>

<template>
    <div class="chatgxy-container" :class="{ 'chatgxy-compact': compact }">
        <div v-if="!compact" class="chatgxy-header">
            <div class="header-main">
                <Heading h2 :icon="faMagic" size="lg">
                    <span>ChatGXY</span>
                </Heading>
                <div class="header-actions">
                    <GButton
                        color="blue"
                        outline
                        size="small"
                        :pressed="usingDataAnalysisAgent"
                        tooltip
                        :title="
                            !usingDataAnalysisAgent
                                ? 'Include datasets from my current history in the conversation context'
                                : 'Do not include datasets'
                        "
                        @click="() => (usingDataAnalysisAgent = !usingDataAnalysisAgent)">
                        <FontAwesomeIcon :icon="faMicroscope" fixed-width />
                        Include Datasets
                    </GButton>
                    <GButton color="blue" outline size="small" tooltip title="Start New Chat" @click="startNewChat">
                        <FontAwesomeIcon :icon="faPlus" fixed-width />
                        New
                    </GButton>
                    <GButton
                        v-if="currentChatId"
                        color="red"
                        outline
                        tooltip
                        title="Delete this conversation"
                        @click="deleteCurrentChat">
                        <FontAwesomeIcon :icon="faTrash" fixed-width />
                    </GButton>
                    <GButton color="blue" outline tooltip title="Open in floating window" @click="popOutToScratchbook">
                        <FontAwesomeIcon :icon="faExternalLinkAlt" fixed-width />
                    </GButton>
                </div>
            </div>

            <div v-if="usingDataAnalysisAgent" class="header-expand">
                <div class="pb-2">Select a dataset for this chat</div>
                <DatasetSelector
                    v-if="datasetOptions.length"
                    id="dataset-select"
                    v-model="selectedDatasetsFormData"
                    :loading="loadingDatasets"
                    :options="formDataOptions"
                    user-defined-title="Created for ChatGXY"
                    workflow-run />
                <BAlert v-else :variant="datasetError ? 'danger' : 'info'" show>
                    <div v-if="loadingDatasets">
                        <LoadingSpan message="Loading datasets" />
                    </div>
                    <div v-else-if="datasetError">{{ datasetError }}</div>
                    <div v-else>
                        No datasets in the current history. Upload a dataset or switch to a different history to use
                        this feature.
                    </div>
                </BAlert>
            </div>
        </div>

        <div ref="chatContainer" class="chat-messages">
            <ChatMessageCell
                v-for="message in messages"
                :key="message.id"
                :message="message"
                :render-markdown="safeRenderMarkdown"
                :processing-action="processingAction"
                @feedback="sendFeedback"
                @handle-action="handleAction">
                <template v-slot:after-content>
                    <BAlert v-if="isAwaitingExecution(message)" variant="warning" show>
                        ⚙️ Analysis still running… please keep this tab open; refreshing will restart the execution.
                    </BAlert>
                    <BAlert
                        v-else-if="message.agentResponse?.metadata?.pyodide_status === 'timeout'"
                        variant="warning"
                        show>
                        ⚠️ Previous run timed out before the result was sent. Please ask again if you still need this
                        step to complete.
                    </BAlert>
                    <MessageArtifacts v-if="hasArtifacts(message)" :message="message" />
                    <MessageIntermediateDetails :message="message" :pyodide-executions="pyodideExecutions" />
                </template>
            </ChatMessageCell>

            <!-- Loading state -->
            <div v-if="busy" class="loading-entry">
                <div class="loading-gutter">
                    <span class="loading-indicator">
                        <FontAwesomeIcon :icon="getAgentIcon(selectedAgentType)" fixed-width />
                    </span>
                </div>
                <div class="loading-body">
                    <BSkeleton animation="wave" width="85%" />
                    <BSkeleton animation="wave" width="55%" />
                    <BSkeleton animation="wave" width="70%" />
                </div>
            </div>
        </div>

        <div class="chatgxy-footer">
            <ChatInput v-model="query" :busy="isChatBusy" @submit="submitQuery" />
        </div>
    </div>
</template>

<style lang="scss" scoped>
@import "@/style/scss/theme/blue.scss";

.chatgxy-container {
    height: calc(100vh - #{$masthead-height} - 2rem);
    display: flex;
    flex-direction: column;
    background: $white;
    border-radius: 0.5rem;
    overflow: hidden;
}

.chatgxy-compact {
    height: 100vh;

    .chat-messages {
        padding: 0.75rem 1rem;
    }

    .chatgxy-footer {
        padding: 0.5rem 0.75rem;
    }
}

.chatgxy-header {
    padding: 0.75rem 1rem;
    background: $panel-bg-color;
    border-bottom: $border-default;

    .header-main {
        display: flex;
        align-items: center;
        justify-content: space-between;
    }

    .header-actions {
        display: flex;
        gap: 0.5rem;
        align-items: stretch;
    }

    .header-expand {
        margin-top: 0.625rem;
        padding-top: 0.625rem;
        border-top: 1px solid rgba($brand-primary, 0.12);
        animation: fadeIn 0.2s ease-out;

        > div:first-child {
            font-size: 0.72rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            color: $text-muted;
        }
    }
}

.chatgxy-footer {
    padding: 0.75rem 1rem;
    background: $panel-bg-color;
    border-top: $border-default;
    box-shadow: 0 -2px 4px rgba(0, 0, 0, 0.05);
}

.chat-messages {
    flex: 1;
    overflow-y: auto;
    padding: 1rem 1.5rem;
    background: $white;
}

// Loading skeleton
.loading-entry {
    display: flex;
    gap: 0;
    margin-top: 1.25rem;
    animation: fadeIn 0.2s ease-out;
}

.loading-gutter {
    flex-shrink: 0;
    width: 2rem;
    padding-top: 0.125rem;
}

.loading-indicator {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 1.5rem;
    height: 1.5rem;
    border-radius: 50%;
    background: rgba($brand-primary, 0.08);
    color: $brand-primary;
    font-size: 0.65rem;
}

.loading-body {
    flex: 1;
    opacity: 0.6;
}

@keyframes fadeIn {
    from {
        opacity: 0;
        transform: translateY(4px);
    }
    to {
        opacity: 1;
        transform: translateY(0);
    }
}
</style>
