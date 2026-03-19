import { nextTick, type Ref } from "vue";

import { GalaxyApi } from "@/api";
import type { ChatHistoryItem, ChatMessage } from "@/components/ChatGXY/types";
import { generateId, scrollToBottom } from "@/components/ChatGXY/utilities";

type PrepareConversationReplay = () => void;
type RebuildConversationMessages = (conversation: unknown[], exchangeId: string) => ChatMessage[];
type ResetConversationState = () => void;

interface UseChatGxyConversationOptions {
    chatContainer: Ref<HTMLElement | undefined>;
    currentChatId: Ref<string | null>;
    hasLoadedInitialChat: Ref<boolean>;
    messages: Ref<ChatMessage[]>;
    query: Ref<string>;
    prepareConversationReplay: PrepareConversationReplay;
    rebuildConversationMessages: RebuildConversationMessages;
    resetConversationState: ResetConversationState;
}

function syncRouteToExchange(exchangeId: string | null) {
    if (typeof window === "undefined") {
        return;
    }
    const currentUrl = new URL(window.location.href);
    const compactValue = currentUrl.searchParams.get("compact");
    const nextPath = exchangeId ? `/chatgxy/${exchangeId}` : "/chatgxy";
    const nextSearch = compactValue !== null ? `?compact=${compactValue}` : "";
    const nextUrl = `${nextPath}${nextSearch}`;
    if (`${currentUrl.pathname}${currentUrl.search}` === nextUrl) {
        return;
    }
    window.history.replaceState(window.history.state, "", nextUrl);
}

export function useChatGxyConversation({
    chatContainer,
    currentChatId,
    hasLoadedInitialChat,
    messages,
    query,
    prepareConversationReplay,
    rebuildConversationMessages,
    resetConversationState,
}: UseChatGxyConversationOptions) {
    async function fetchConversation(exchangeId: string): Promise<boolean> {
        prepareConversationReplay();

        const { data: fullConversation } = await GalaxyApi().GET(`/api/chat/exchange/{exchange_id}/messages`, {
            params: {
                path: { exchange_id: exchangeId },
            },
        });

        if (!fullConversation || fullConversation.length === 0) {
            return false;
        }

        messages.value = rebuildConversationMessages(fullConversation, exchangeId);
        currentChatId.value = exchangeId;
        nextTick(() => scrollToBottom(chatContainer.value));
        return true;
    }

    async function loadChatById(exchangeId: string) {
        try {
            const loaded = await fetchConversation(exchangeId);
            if (loaded) {
                hasLoadedInitialChat.value = true;
            }
        } catch (error) {
            console.error("Failed to load chat by ID:", error);
        }
    }

    async function loadLatestChat() {
        try {
            const { data, error } = await GalaxyApi().GET("/api/chat/history", {
                params: {
                    query: { limit: 1 },
                },
            });

            if (data && !error && data.length > 0) {
                const latestChat = data[0] as unknown as ChatHistoryItem;
                try {
                    const loaded = await fetchConversation(latestChat.id);
                    if (loaded) {
                        hasLoadedInitialChat.value = true;
                    }
                } catch (conversationError) {
                    console.error("Error loading latest conversation:", conversationError);
                }
            }
        } catch (error) {
            console.error("Failed to load latest chat:", error);
        }
    }

    function startNewChat() {
        resetConversationState();
        messages.value = [
            {
                id: generateId(),
                role: "assistant",
                content: "New conversation started. How can I help?",
                timestamp: new Date(),
                agentType: "router",
                confidence: "high",
                feedback: null,
                isSystemMessage: true,
            },
        ];
        currentChatId.value = null;
        query.value = "";
        syncRouteToExchange(null);
    }

    async function deleteCurrentChat() {
        if (!currentChatId.value) {
            return;
        }
        try {
            const { error } = await GalaxyApi().DELETE("/api/chat/exchange/{exchange_id}", {
                params: { path: { exchange_id: currentChatId.value } },
            });
            if (!error) {
                startNewChat();
            }
        } catch (deleteError) {
            console.error("Failed to delete chat:", deleteError);
        }
    }

    return {
        deleteCurrentChat,
        loadChatById,
        loadLatestChat,
        startNewChat,
        syncRouteToExchange,
    };
}
