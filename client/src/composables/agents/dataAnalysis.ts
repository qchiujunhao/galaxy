import { storeToRefs } from "pinia";
import { computed, reactive, type Ref, ref, watch } from "vue";

import { GalaxyApi } from "@/api";
import type { ChatMessage, ConfidenceLevel, ExecutionState, UploadedArtifact } from "@/components/ChatGXY/types";
import {
    applyCollapseState,
    formatGeneratedEntry,
    generateId,
    normaliseAnalysisSteps,
    normaliseArtifactList,
    normalisePathList,
    resolveDownloadUrl,
} from "@/components/ChatGXY/utilities";
import type { DataOption } from "@/components/Form/Elements/FormData/types";
import { getAppRoot } from "@/onload";
import { useHistoryStore } from "@/stores/historyStore";
import { errorMessageAsString } from "@/utils/simple-error";

import { useToast } from "../toast";
import { useHistoryDatasets } from "../useHistoryDatasets";
import { type PyodideArtifact, type PyodideRunResult, type PyodideTask, usePyodideRunner } from "../usePyodideRunner";

interface ConversationMessageEntry {
    agent_response?: {
        agent_type?: string;
        confidence?: ConfidenceLevel;
        metadata?: ConversationMetadata;
        suggestions?: string[];
    };
    agent_type?: string;
    content?: string;
    feedback?: number | null;
    role?: string;
    task_id?: string;
    timestamp?: string;
}

interface ConversationMetadata {
    analysis_steps?: unknown;
    artifacts?: unknown;
    executed_task?: {
        task_id?: string;
    };
    execution?: {
        artifacts?: unknown;
    };
    files?: unknown;
    plots?: unknown;
    pyodide_task?: {
        task_id?: string;
    };
}

/** Composable that is enabled if we are using the Data Analysis agent in ChatGXY
 * @param messages - Reactive reference to the chat messages array
 * @param currentChatId - Reactive reference to the current chat/exchange ID
 * @param selectedAgentType - Reactive reference to the currently selected agent type
 */
export function useDataAnalysisAgent(
    enabled: Ref<boolean>,
    messages: Ref<ChatMessage[]>,
    currentChatId: Ref<string | null>,
    selectedAgentType: Ref<string>,
) {
    const Toast = useToast();

    const pyodideRunner = usePyodideRunner();
    const pyodideExecutions = reactive<Record<string, ExecutionState>>({});
    const chatStream = ref<WebSocket | null>(null);
    const streamSupported = typeof window !== "undefined" && typeof WebSocket !== "undefined";
    const deliveredTaskIds = new Set<string>();
    const pyodideTaskToMessage = new Map<string, ChatMessage>();
    const pendingCollapsedMessages: ChatMessage[] = [];

    const selectedDatasets = ref<string[]>([]);

    const { currentHistoryId } = storeToRefs(useHistoryStore());

    const {
        datasets: currentHistoryDatasets,
        isFetching: loadingDatasets,
        error: datasetError,
    } = useHistoryDatasets({
        historyId: () => currentHistoryId.value || "",
        enabled: () => currentHistoryId.value !== null && enabled.value,
    });

    const datasetOptions = computed<DataOption[]>(() => {
        if (loadingDatasets.value) {
            return [];
        }
        return currentHistoryDatasets.value
            .map((item) => {
                const name = item.name || `Dataset ${item.hid || ""}`;
                const hidden = Boolean(item.visible === false);
                const generated = typeof name === "string" && name.trim().toLowerCase().startsWith("generated_file");
                if (!item.id || hidden || generated) {
                    return null;
                }
                return { id: item.id, hid: item.hid, name, src: "hda" } as DataOption;
            })
            .filter((entry): entry is DataOption => entry !== null && Boolean(entry));
    });

    const selectedDatasetsFormData = computed<{ values: Array<DataOption> } | null>({
        get() {
            if (selectedDatasets.value.length === 0) {
                return null;
            }
            const selected = datasetOptions.value.filter((dataset) => selectedDatasets.value.includes(dataset.id));
            return { values: selected };
        },
        set(value) {
            if (value === null) {
                selectedDatasets.value = [];
            } else {
                selectedDatasets.value = value.values.map((dataset) => dataset.id);
            }
        },
    });

    const formDataOptions = computed<Record<string, Array<DataOption>>>(() => ({ hda: datasetOptions.value }));

    const pyodideRunnerRunning = computed(() => pyodideRunner.isRunning.value);

    // Methods

    function mergeUniquePaths(...groups: Array<string[] | undefined>): string[] | undefined {
        const merged: string[] = [];
        const seen = new Set<string>();
        for (const group of groups) {
            for (const entry of group || []) {
                if (!entry || seen.has(entry)) {
                    continue;
                }
                seen.add(entry);
                merged.push(entry);
            }
        }
        return merged.length ? merged : undefined;
    }

    function mergeArtifacts(...groups: Array<UploadedArtifact[] | undefined>): UploadedArtifact[] | undefined {
        const merged: UploadedArtifact[] = [];
        const seen = new Set<string>();
        for (const group of groups) {
            for (const artifact of group || []) {
                const key = artifact.dataset_id || artifact.name || artifact.download_url || generateId();
                if (seen.has(key)) {
                    continue;
                }
                seen.add(key);
                merged.push(artifact);
            }
        }
        return merged.length ? merged : undefined;
    }

    function mergeMessageOutputs(target: ChatMessage, source: ChatMessage) {
        target.artifacts = mergeArtifacts(target.artifacts, source.artifacts);
        target.generatedPlots = mergeUniquePaths(target.generatedPlots, source.generatedPlots);
        target.generatedFiles = mergeUniquePaths(target.generatedFiles, source.generatedFiles);
    }

    function applyLatestAssistantState(target: ChatMessage, latest: ChatMessage) {
        target.content = latest.content;
        target.timestamp = latest.timestamp;
        target.agentType = latest.agentType;
        target.confidence = latest.confidence;
        target.feedback = latest.feedback;
        target.agentResponse = latest.agentResponse;
        target.suggestions = latest.suggestions;
        target.analysisSteps = latest.analysisSteps;
        target.routingInfo = latest.routingInfo;
        target.isCollapsible = latest.isCollapsible;
        if (latest.isCollapsed !== undefined) {
            target.isCollapsed = latest.isCollapsed;
        }
    }

    function appendAssistantMessage(payload: any, fallbackAgentType: string): ChatMessage {
        const existingMessage = findMessageForPayload(payload);
        if (existingMessage) {
            populateAssistantMessage(existingMessage, payload, fallbackAgentType, { skipDatasetUpdate: true });
            if (!existingMessage.isCollapsible) {
                attachPendingCollapsedMessages(existingMessage);
            }
            maybeRunPyodideForMessage(existingMessage);
            return existingMessage;
        }

        const assistantMessage: ChatMessage = {
            id: generateId(),
            role: "assistant",
            content: "",
            timestamp: new Date(),
            agentType: fallbackAgentType === "auto" ? "router" : fallbackAgentType,
            confidence: "medium",
            feedback: null,
        };

        populateAssistantMessage(assistantMessage, payload, fallbackAgentType);

        if (assistantMessage.isCollapsible) {
            pendingCollapsedMessages.push(assistantMessage);
            return assistantMessage;
        }

        attachPendingCollapsedMessages(assistantMessage);
        messages.value.push(assistantMessage);

        if (payload?.exchange_id) {
            currentChatId.value = payload.exchange_id;
        }

        maybeRunPyodideForMessage(assistantMessage);
        ensurePendingPyodideTasks();

        return assistantMessage;
    }

    function applyDatasetSelectionFromMessages(conversation: any[]) {
        for (let i = conversation.length - 1; i >= 0; i -= 1) {
            const entry = conversation[i];
            const datasets = entry?.dataset_ids;
            if (Array.isArray(datasets) && datasets.length > 0) {
                selectedDatasets.value = datasets.map(String);
                return;
            }
        }
    }

    function prepareConversationReplay() {
        pendingCollapsedMessages.length = 0;
        deliveredTaskIds.clear();
        pyodideTaskToMessage.clear();
    }

    function resetConversationState() {
        prepareConversationReplay();
        Object.keys(pyodideExecutions).forEach((key) => delete pyodideExecutions[key]);
        selectedDatasets.value = [];
    }

    function applyExecutionResultMetadata(message: ChatMessage, execResult: any) {
        if (!execResult) {
            return;
        }
        if (!message.agentResponse) {
            message.agentResponse = {
                metadata: {},
                suggestions: [],
                agent_type: message.agentType || selectedAgentType.value,
                confidence: message.confidence || "medium",
                content: message.content,
            };
        }
        const metadata = message.agentResponse?.metadata || {};
        metadata.pyodide_status = execResult.success ? "completed" : "error";
        metadata.stdout = execResult.stdout || "";
        metadata.stderr = execResult.stderr || "";
        metadata.execution = {
            success: execResult.success,
            stdout: execResult.stdout || "",
            stderr: execResult.stderr || "",
            artifacts: execResult.artifacts || [],
            task_id: execResult.task_id,
        };
        if (metadata.pyodide_task && execResult.task_id) {
            metadata.executed_task = {
                code: metadata.executed_task?.code || "",
                timeout_seconds: metadata.executed_task?.timeout_seconds || 30,
                task_id: execResult.task_id,
            };
            pyodideTaskToMessage.set(String(execResult.task_id), message);
            deliveredTaskIds.add(String(execResult.task_id));
        }
        if (metadata.pyodide_task) {
            delete metadata.pyodide_task;
        }
        const artifacts = normaliseArtifactList(execResult.artifacts);
        updateMessageOutputsFromArtifacts(message, artifacts);
        applyCollapseState(message);
    }

    function attachPendingCollapsedMessages(target: ChatMessage) {
        if (!pendingCollapsedMessages.length) {
            return;
        }
        const existingHistory = target.collapsedHistory ? [...target.collapsedHistory] : [];
        target.collapsedHistory = [...existingHistory, ...pendingCollapsedMessages].map((msg) => {
            msg.isCollapsed = true;
            return msg;
        });
        for (let i = pendingCollapsedMessages.length - 1; i >= 0; i -= 1) {
            const msg = pendingCollapsedMessages[i];
            if (msg) {
                mergeMessageOutputs(target, msg);
            }
        }
        if (target.isCollapsed === undefined) {
            target.isCollapsed = true;
        }
        pendingCollapsedMessages.length = 0;
    }

    function getLatestUserQuery(): string {
        for (let i = messages.value.length - 1; i >= 0; i -= 1) {
            const entry = messages.value[i];
            if (entry?.role === "user") {
                return entry.content;
            }
        }
        return "";
    }

    function findMessageForPayload(payload: any): ChatMessage | undefined {
        const candidateTaskId =
            payload?.task_id ||
            payload?.pyodide_task_id ||
            payload?.agent_response?.metadata?.executed_task?.task_id ||
            payload?.agent_response?.metadata?.pyodide_task?.task_id;
        if (candidateTaskId) {
            const key = String(candidateTaskId);
            if (pyodideTaskToMessage.has(key)) {
                return pyodideTaskToMessage.get(key);
            }
            const fallback = messages.value.find((message) => {
                const metadata = message.agentResponse?.metadata;
                if (!metadata) {
                    return false;
                }
                const executedTask = metadata.executed_task;
                const pendingTask = metadata.pyodide_task;
                return (
                    (executedTask && String(executedTask.task_id) === key) ||
                    (pendingTask && String(pendingTask.task_id) === key)
                );
            });
            if (fallback) {
                pyodideTaskToMessage.set(key, fallback);
                return fallback;
            }
        }
        return undefined;
    }

    function rebuildConversationMessages(conversation: unknown[], exchangeId: string): ChatMessage[] {
        const taskIdToMessage: Record<string, ChatMessage> = {};
        const pendingExecResults: Record<string, unknown> = {};
        const assistantMessagesToReplay: ChatMessage[] = [];
        const rebuiltMessages: ChatMessage[] = [];
        let currentTurnAssistant: ChatMessage | null = null;

        for (const [index, entry] of conversation.entries()) {
            const messageEntry = entry as ConversationMessageEntry;
            if (messageEntry.role === "execution_result") {
                if (messageEntry.task_id) {
                    deliveredTaskIds.add(String(messageEntry.task_id));
                    const target = taskIdToMessage[String(messageEntry.task_id)];
                    if (target) {
                        applyExecutionResultMetadata(target, messageEntry);
                    } else {
                        pendingExecResults[String(messageEntry.task_id)] = messageEntry;
                    }
                }
                continue;
            }

            if (messageEntry.role !== "user" && messageEntry.role !== "assistant") {
                continue;
            }

            const message: ChatMessage = {
                id: `hist-${messageEntry.role}-${exchangeId}-${index}`,
                role: messageEntry.role,
                content: messageEntry.content || "",
                timestamp: messageEntry.timestamp ? new Date(messageEntry.timestamp) : new Date(),
                feedback: null,
            };

            if (messageEntry.role === "user") {
                currentTurnAssistant = null;
                rebuiltMessages.push(message);
                continue;
            }

            message.agentType = messageEntry.agent_response?.agent_type || messageEntry.agent_type;
            message.confidence = messageEntry.agent_response?.confidence || "medium";
            message.feedback = messageEntry.feedback === 1 ? "up" : messageEntry.feedback === 0 ? "down" : null;

            if (messageEntry.agent_response) {
                message.agentResponse = messageEntry.agent_response;
                message.suggestions = messageEntry.agent_response.suggestions || [];
                const metadata = messageEntry.agent_response.metadata;
                const steps = metadata ? normaliseAnalysisSteps(metadata.analysis_steps) : [];
                if (steps.length) {
                    message.analysisSteps = steps;
                }
                if (metadata) {
                    const artifactSource = metadata.artifacts ?? metadata.execution?.artifacts;
                    const storedArtifacts = normaliseArtifactList(artifactSource);
                    updateMessageOutputsFromArtifacts(message, storedArtifacts);
                    const plots = normalisePathList(metadata.plots);
                    message.generatedPlots = plots.length ? plots : undefined;
                    const files = normalisePathList(metadata.files);
                    message.generatedFiles = files.length ? files : undefined;
                    const executedTask = metadata.executed_task;
                    const pendingTask = metadata.pyodide_task;
                    const taskIdsToCheck = [executedTask?.task_id, pendingTask?.task_id].filter(Boolean) as string[];

                    const taskTarget =
                        currentTurnAssistant &&
                        isDataAnalysisMessage(currentTurnAssistant) &&
                        isDataAnalysisMessage(message)
                            ? currentTurnAssistant
                            : message;

                    if (executedTask?.task_id) {
                        const taskId = String(executedTask.task_id);
                        deliveredTaskIds.add(taskId);
                        pyodideTaskToMessage.set(taskId, taskTarget);
                        taskIdToMessage[taskId] = taskTarget;
                    }
                    if (pendingTask?.task_id) {
                        const taskId = String(pendingTask.task_id);
                        pyodideTaskToMessage.set(taskId, taskTarget);
                        taskIdToMessage[taskId] = taskTarget;
                    }
                    taskIdsToCheck.forEach((taskId) => {
                        if (pendingExecResults[taskId]) {
                            applyExecutionResultMetadata(taskTarget, pendingExecResults[taskId]);
                            delete pendingExecResults[taskId];
                        }
                    });
                }
            }

            applyCollapseState(message);

            if (currentTurnAssistant && isDataAnalysisMessage(currentTurnAssistant) && isDataAnalysisMessage(message)) {
                const history = currentTurnAssistant.collapsedHistory ? [...currentTurnAssistant.collapsedHistory] : [];
                message.isCollapsed = true;
                history.push(message);
                currentTurnAssistant.collapsedHistory = history;
                updateMessageOutputsFromArtifacts(currentTurnAssistant, message.artifacts);
                currentTurnAssistant.generatedPlots = [
                    ...(currentTurnAssistant.generatedPlots || []),
                    ...(message.generatedPlots || []).filter(
                        (entry) => !(currentTurnAssistant.generatedPlots || []).includes(entry),
                    ),
                ];
                currentTurnAssistant.generatedFiles = [
                    ...(currentTurnAssistant.generatedFiles || []),
                    ...(message.generatedFiles || []).filter(
                        (entry) => !(currentTurnAssistant.generatedFiles || []).includes(entry),
                    ),
                ];
                applyLatestAssistantState(currentTurnAssistant, message);
                continue;
            }

            currentTurnAssistant = message;
            rebuiltMessages.push(message);
            assistantMessagesToReplay.push(message);
        }

        applyDatasetSelectionFromMessages(conversation);
        assistantMessagesToReplay.forEach((assistantMessage) => maybeRunPyodideForMessage(assistantMessage));
        if (pendingCollapsedMessages.length) {
            pendingCollapsedMessages.forEach((message) => rebuiltMessages.push(message));
            pendingCollapsedMessages.length = 0;
        }

        return rebuiltMessages;
    }

    // TODO: Unused method, remove if never needed now
    // function loadSingleMessageFallback(item: ChatHistoryItem) {
    //     const userMessage: ChatMessage = {
    //         id: `hist-user-${item.id}`,
    //         role: "user",
    //         content: typeof item.query === "string" ? item.query : String(item.query ?? ""),
    //         timestamp: new Date(item.timestamp),
    //         feedback: null,
    //     };

    //     const assistantMessage: ChatMessage = {
    //         id: `hist-assistant-${item.id}`,
    //         role: "assistant",
    //         content: typeof item.response === "string" ? item.response : String(item.response ?? ""),
    //         timestamp: new Date(item.timestamp),
    //         agentType: item.agent_type,
    //         confidence: item.agent_response?.confidence || "medium",
    //         feedback: item.feedback === 1 ? "up" : item.feedback === 0 ? "down" : null,
    //     };

    //     if (item.agent_response) {
    //         assistantMessage.agentResponse = item.agent_response;
    //         assistantMessage.suggestions = item.agent_response.suggestions || [];
    //         const metadata = item.agent_response?.metadata;
    //         const steps = metadata ? normaliseAnalysisSteps(metadata?.analysis_steps) : [];
    //         if (steps.length) {
    //             assistantMessage.analysisSteps = steps;
    //         }
    //         if (metadata) {
    //             const artifactSource = metadata?.artifacts ?? metadata?.execution?.artifacts;
    //             const storedArtifacts = normaliseArtifactList(artifactSource);
    //             updateMessageOutputsFromArtifacts(assistantMessage, storedArtifacts);
    //             const plots = normalisePathList(metadata?.plots);
    //             if (plots.length) {
    //                 assistantMessage.generatedPlots = plots;
    //             }
    //             const files = normalisePathList(metadata?.files);
    //             if (files.length) {
    //                 assistantMessage.generatedFiles = files;
    //             }
    //             const executedTask = metadata?.executed_task;
    //             if (executedTask?.task_id) {
    //                 deliveredTaskIds.add(String(executedTask.task_id));
    //                 pyodideTaskToMessage.set(String(executedTask.task_id), assistantMessage);
    //             }
    //             const pendingTask = metadata?.pyodide_task;
    //             if (pendingTask?.task_id) {
    //                 pyodideTaskToMessage.set(String(pendingTask.task_id), assistantMessage);
    //             }
    //         }
    //     }

    //     applyCollapseState(assistantMessage);
    //     messages.value = [userMessage, assistantMessage];
    //     currentChatId.value = item.id;
    //     nextTick(() => scrollToBottom(chatContainer.value));
    //     maybeRunPyodideForMessage(assistantMessage);
    // }

    function maybeRunPyodideForMessage(message: ChatMessage) {
        const metadata = message.agentResponse?.metadata;
        if (!metadata) {
            return;
        }
        const task = metadata.pyodide_task;
        if (!task) {
            return;
        }
        const taskKey = task.task_id || message.id;
        if (task.task_id) {
            pyodideTaskToMessage.set(String(task.task_id), message);
        }
        if (task.task_id && deliveredTaskIds.has(task.task_id)) {
            metadata.pyodide_status = metadata.pyodide_status || "completed";
            return;
        }
        const existing = pyodideExecutions[taskKey];
        if (existing) {
            if (existing.status === "completed" || existing.status === "error") {
                metadata.pyodide_status = existing.status;
                return;
            }
            return;
        }

        const status = metadata.pyodide_status;
        if (status === "error" || status === "completed" || status === "timeout") {
            delete metadata.pyodide_task;
            const baseState: ExecutionState = {
                status: status === "completed" ? "completed" : "error",
                stdout: metadata.stdout || "",
                stderr: metadata.stderr || "",
                artifacts: [],
            };
            if (status === "error") {
                baseState.errorMessage = metadata.error || "";
            } else if (status === "timeout") {
                baseState.errorMessage =
                    metadata.pyodide_timeout_reason ||
                    `Execution timed out after ${metadata.pyodide_timeout_seconds || "unknown"} seconds.`;
            }
            pyodideExecutions[taskKey] = baseState;
            return;
        }
        if (status && status !== "pending") {
            return;
        }

        if (!pyodideRunner.isSupported.value) {
            pyodideExecutions[taskKey] = {
                status: "error",
                stdout: "",
                stderr: "",
                artifacts: [],
                errorMessage: "Browser does not support in-browser execution.",
            };
            metadata.pyodide_status = "error";
            Toast.warning("This browser cannot run the generated analysis code.");
            return;
        }

        metadata.pyodide_retry_count = (metadata.pyodide_retry_count || 0) + 1;
        if (metadata.pyodide_retry_count > 1) {
            metadata.pyodide_status = "error";
            pyodideExecutions[taskKey] = {
                status: "error",
                stdout: "",
                stderr: "",
                artifacts: [],
                errorMessage: "Pyodide task exceeded retry limit.",
            };
            return;
        }

        runPyodideTaskForMessage(message, task, taskKey, metadata);
    }

    function populateAssistantMessage(
        target: ChatMessage,
        payload: any,
        fallbackAgentType: string,
        options?: { skipDatasetUpdate?: boolean },
    ) {
        const agentResponse = (payload?.agent_response ?? payload?.response?.agent_response) as any;
        const rawContent =
            typeof payload === "string"
                ? payload
                : (payload?.response ?? agentResponse?.content ?? "No response received");
        const content = typeof rawContent === "string" ? rawContent : String(rawContent ?? "No response received");
        const effectiveAgentType =
            agentResponse?.agent_type || (fallbackAgentType === "auto" ? "router" : fallbackAgentType);

        target.content = content;
        target.timestamp = payload?.timestamp ? new Date(payload.timestamp) : new Date();
        target.agentType = effectiveAgentType;
        target.confidence = agentResponse?.confidence || (payload?.confidence as ConfidenceLevel) || "medium";
        target.feedback = target.feedback ?? null;
        target.agentResponse = agentResponse;
        target.suggestions = agentResponse?.suggestions;
        target.routingInfo = payload?.routing_info;

        const metadata = agentResponse?.metadata;
        if (metadata) {
            const steps = normaliseAnalysisSteps(metadata.analysis_steps);
            if (steps.length) {
                target.analysisSteps = steps;
            }
            const artifactSource = metadata.artifacts ?? metadata.execution?.artifacts;
            const storedArtifacts = normaliseArtifactList(artifactSource);
            updateMessageOutputsFromArtifacts(target, storedArtifacts);
            const plotEntries = normalisePathList(metadata.plots);
            if (plotEntries.length) {
                target.generatedPlots = plotEntries;
            }
            const fileEntries = normalisePathList(metadata.files);
            if (fileEntries.length) {
                target.generatedFiles = fileEntries;
            }
            const executedTask = metadata.executed_task;
            if (executedTask?.task_id) {
                const taskId = String(executedTask.task_id);
                deliveredTaskIds.add(taskId);
                pyodideTaskToMessage.set(taskId, target);
            }
            const pendingTask = metadata.pyodide_task;
            if (pendingTask?.task_id) {
                pyodideTaskToMessage.set(String(pendingTask.task_id), target);
            }
        }

        if (!options?.skipDatasetUpdate) {
            if (Array.isArray(payload?.dataset_ids) && payload.dataset_ids.length > 0) {
                selectedDatasets.value = payload.dataset_ids.map(String);
            }
        }

        applyCollapseState(target);
    }

    function runPyodideTaskForMessage(
        message: ChatMessage,
        task: PyodideTask,
        taskKey: string,
        metadata: Record<string, any>,
    ) {
        const state = reactive<ExecutionState>({
            status: "initialising",
            stdout: "",
            stderr: "",
            artifacts: [],
        });
        pyodideExecutions[taskKey] = state;
        metadata.pyodide_status = "running";

        const runnerTask: PyodideTask = { ...task, task_id: taskKey };

        let executionPromise: Promise<any>;
        try {
            executionPromise = pyodideRunner.runTask(runnerTask, {
                onStdout: (line) => {
                    state.stdout += line;
                },
                onStderr: (line) => {
                    state.stderr += line;
                },
                onStatus: (event) => {
                    state.status = mapStatus(event.status);
                },
            });
        } catch (error) {
            const errMessage = error instanceof Error ? error.message : String(error);
            state.status = "error";
            state.errorMessage = errMessage;
            metadata.pyodide_status = "error";
            if (metadata.pyodide_task) {
                delete metadata.pyodide_task;
            }
            Toast.error(`Pyodide execution failed: ${errMessage}`);
            return;
        }

        executionPromise
            .then(async (result) => {
                state.stdout = result.stdout;
                state.stderr = result.stderr;
                state.status = "submitting";
                const uploadedArtifacts: UploadedArtifact[] = await uploadArtifacts(result.artifacts || []);
                state.artifacts = uploadedArtifacts;
                updateMessageOutputsFromArtifacts(message, uploadedArtifacts);
                await submitPyodideExecutionResult(runnerTask, message, result, uploadedArtifacts);
                applyExecutionResultMetadata(message, {
                    success: result.success,
                    stdout: result.stdout,
                    stderr: result.stderr,
                    artifacts: serializeUploadedArtifacts(uploadedArtifacts),
                    task_id: runnerTask.task_id,
                });
                state.status = result.success ? "completed" : "error";
                if (!result.success && result.error) {
                    state.errorMessage = result.error;
                }
                metadata.pyodide_status = state.status;
            })
            .catch((error) => {
                state.status = "error";
                state.errorMessage = errorMessageAsString(error);
                metadata.pyodide_status = "error";
                if (metadata.pyodide_task) {
                    delete metadata.pyodide_task;
                }
                Toast.error(`Pyodide execution failed: ${state.errorMessage}`);
            });
    }

    function openChatStream(exchangeId: string) {
        if (!streamSupported) {
            return;
        }
        const existing = chatStream.value;
        if (existing) {
            const existingId = (existing as any)._exchangeId as string | undefined;
            if (
                existingId === exchangeId &&
                (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING)
            ) {
                return;
            }
            existing.close();
        }

        try {
            const rawRoot = getAppRoot() || "/";
            const appRoot = rawRoot.replace(/\/+$/, "");
            const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
            const wsUrl = `${protocol}//${window.location.host}${appRoot}/api/chat/exchange/${exchangeId}/stream`;
            const socket = new WebSocket(wsUrl);
            (socket as any)._exchangeId = exchangeId;
            socket.onopen = () => {
                chatStream.value = socket;
            };
            socket.onmessage = (event: MessageEvent) => {
                handleStreamMessage(event);
            };
            socket.onclose = () => {
                if (chatStream.value === socket) {
                    chatStream.value = null;
                }
            };
            socket.onerror = () => {
                socket.close();
            };
        } catch (error) {
            console.error("Failed to open chat stream", error);
        }
    }

    function closeChatStream() {
        const socket = chatStream.value;
        if (socket) {
            chatStream.value = null;
            try {
                socket.close();
            } catch (error) {
                console.error("Failed to close chat stream", error);
            }
        }
    }

    function handleStreamMessage(event: MessageEvent) {
        try {
            const payload = JSON.parse(event.data);
            if (payload?.type === "exec_followup" && payload.payload) {
                const taskId = payload.task_id as string | undefined;
                if (taskId && deliveredTaskIds.has(taskId)) {
                    return;
                }
                if (taskId) {
                    deliveredTaskIds.add(taskId);
                    const existing = pyodideTaskToMessage.get(String(taskId));
                    if (existing) {
                        populateAssistantMessage(existing, payload.payload, selectedAgentType.value, {
                            skipDatasetUpdate: true,
                        });
                        maybeRunPyodideForMessage(existing);
                        return;
                    }
                }
                appendAssistantMessage(payload.payload, selectedAgentType.value);
            }
        } catch (error) {
            console.error("Failed to process chat stream message", error);
        }
    }

    function updateMessageOutputsFromArtifacts(message: ChatMessage, artifacts: UploadedArtifact[] | undefined) {
        if (!artifacts || artifacts.length === 0) {
            return;
        }
        message.artifacts = mergeArtifacts(message.artifacts, artifacts);
        const plotNames: string[] = [];
        const fileNames: string[] = [];
        for (const artifact of artifacts) {
            const name = formatGeneratedEntry(artifact.name || artifact.dataset_id || "");
            if (!name) {
                continue;
            }
            if (artifact.mime_type && artifact.mime_type.startsWith("image/")) {
                plotNames.push(`generated_file/${name}`);
            } else {
                fileNames.push(`generated_file/${name}`);
            }
        }
        message.generatedPlots = mergeUniquePaths(message.generatedPlots, plotNames);
        message.generatedFiles = mergeUniquePaths(message.generatedFiles, fileNames);
    }

    function serializeUploadedArtifacts(artifacts: UploadedArtifact[] = []): UploadedArtifact[] {
        return artifacts.map((artifact) => ({
            dataset_id: artifact.dataset_id,
            name: artifact.name,
            size: artifact.size,
            mime_type: artifact.mime_type,
            download_url: artifact.download_url,
            history_id: artifact.history_id,
        }));
    }

    async function uploadArtifacts(artifacts: PyodideArtifact[]): Promise<UploadedArtifact[]> {
        if (!currentChatId.value) {
            throw new Error("No active chat to attach artifacts.");
        }
        if (!artifacts || artifacts.length === 0) {
            return [];
        }

        const results: UploadedArtifact[] = [];
        for (const artifact of artifacts) {
            const buffer = artifact.buffer;
            if (!buffer) {
                continue;
            }
            const blob = new Blob([buffer], { type: artifact.mime_type || "application/octet-stream" });
            if (blob.size === 0) {
                continue;
            }

            const name = artifact.name || "artifact";
            const mimeType = artifact.mime_type || blob.type || "application/octet-stream";

            // Build multipart explicitly to ensure FastAPI receives `file: UploadFile = File(...)`.
            // The OpenAPI schema currently types `file` as string (format:binary limitation), so relying
            // on automatic serialization can incorrectly send this as a text field instead of a file part.
            const form = new FormData();
            form.append("file", blob, name);
            form.append("name", name);
            form.append("mime_type", mimeType);
            form.append("size", String(blob.size));

            const rawRoot = getAppRoot() || "";
            // Strip trailing slashes; if root is "/" this becomes "" so we don't accidentally build "//api/..."
            const appRoot = rawRoot.replace(/\/+$/, "");
            const url = `${appRoot}/api/chat/exchange/${currentChatId.value}/artifacts`;

            let response: Response;
            try {
                response = await fetch(url, {
                    method: "POST",
                    credentials: "include",
                    body: form,
                });
            } catch (error) {
                throw new Error(`Failed to upload artifact (network error) to ${url}: ${errorMessageAsString(error)}`);
            }

            if (!response.ok) {
                let detail = "";
                try {
                    const payload = await response.json();
                    detail = payload?.err_msg || payload?.detail || JSON.stringify(payload);
                } catch {
                    try {
                        detail = await response.text();
                    } catch {
                        detail = "";
                    }
                }
                throw new Error(`Failed to upload artifact${detail ? `: ${detail}` : ""}`);
            }

            const uploadedPayload = (await response.json()) as UploadedArtifact;

            if (uploadedPayload.download_url) {
                uploadedPayload.download_url = resolveDownloadUrl(uploadedPayload.download_url);
            }
            results.push(uploadedPayload);
            artifact.buffer = undefined;
        }
        return results;
    }

    function mapStatus(status: string): ExecutionState["status"] {
        switch (status) {
            case "initialising":
                return "initialising";
            case "installing":
                return "installing";
            case "fetch":
                return "fetching";
            case "executing":
                return "running";
            case "collecting":
                return "submitting";
            default:
                return "running";
        }
    }

    async function submitPyodideExecutionResult(
        task: PyodideTask,
        message: ChatMessage,
        result: PyodideRunResult,
        artifacts: UploadedArtifact[],
    ) {
        if (!currentChatId.value) {
            throw new Error("No active chat to submit execution results.");
        }

        const useStream = streamSupported && chatStream.value?.readyState === WebSocket.OPEN;

        const payload = {
            task_id: task.task_id,
            stdout: result.stdout,
            stderr: result.stderr,
            artifacts,
            success: result.success,
            metadata: {
                selected_dataset_ids: [...selectedDatasets.value],
                agent_type: message.agentResponse?.agent_type || message.agentType,
                original_query: getLatestUserQuery(),
            },
        };

        const { data, error } = await GalaxyApi().POST(`/api/chat/exchange/{exchange_id}/pyodide_result`, {
            params: {
                path: { exchange_id: currentChatId.value },
            },
            body: payload,
        });

        if (error) {
            throw new Error(errorMessageAsString(error, "Failed to submit execution results"));
        }

        if (!useStream && data) {
            if (payload.task_id) {
                deliveredTaskIds.add(payload.task_id);
            }
            appendAssistantMessage(data, message.agentType || selectedAgentType.value);
        }
    }

    function ensurePendingPyodideTasks() {
        messages.value.forEach((message) => {
            const metadata = message.agentResponse?.metadata;
            if (!metadata) {
                return;
            }
            const task = metadata.pyodide_task;
            if (!task) {
                return;
            }
            const status = metadata.pyodide_status || "pending";
            if (status === "error" || status === "completed" || status === "timeout") {
                return;
            }
            const taskKey = task.task_id || message.id;
            if (pyodideExecutions[taskKey]) {
                return;
            }
            if (task.task_id && deliveredTaskIds.has(task.task_id)) {
                return;
            }
            maybeRunPyodideForMessage(message);
        });
    }

    watch(
        currentChatId,
        (newId, oldId) => {
            if (!streamSupported) {
                return;
            }
            if (oldId && newId !== oldId) {
                closeChatStream();
                deliveredTaskIds.clear();
            }
            if (typeof newId === "string") {
                openChatStream(newId);
            }
            if (newId == null) {
                deliveredTaskIds.clear();
            }
        },
        { immediate: false },
    );

    watch(
        messages,
        () => {
            ensurePendingPyodideTasks();
        },
        { deep: true },
    );

    watch(
        () => datasetOptions.value,
        () => {
            const validIds = new Set(datasetOptions.value.map((entry) => entry.id));
            selectedDatasets.value = selectedDatasets.value.filter((id) => validIds.has(id));
        },
        { immediate: true, deep: true },
    );

    watch(
        () => currentHistoryId.value,
        () => {
            selectedDatasets.value = [];
        },
        { immediate: false },
    );

    return {
        /** Append or merge an assistant response through the shared normalization path */
        appendAssistantMessage,
        /** Apply dataset selection from messages */
        applyDatasetSelectionFromMessages,
        /** Apply execution result metadata to a message, updating its state and outputs accordingly */
        applyExecutionResultMetadata,
        /** Clear replay-only state before rebuilding a conversation from persisted messages */
        prepareConversationReplay,
        /** Attach pending collapsed messages to the chat */
        attachPendingCollapsedMessages,
        /** Function to close the chat stream WebSocket connection */
        closeChatStream,
        /** Error related to fetching datasets from the current history */
        datasetError,
        /** Available dataset options for selection */
        datasetOptions,
        /** Set of task IDs for which we've already delivered updates to avoid duplicates */
        deliveredTaskIds,
        /** Options for the `FormData` component */
        formDataOptions,
        /** Whether datasets are currently being loaded */
        loadingDatasets,
        /** Attempt to run Pyodide for a given message */
        maybeRunPyodideForMessage,
        /** Array of messages that are pending Pyodide execution and should be rendered in a collapsed state until execution completes */
        pendingCollapsedMessages,
        /** Reactive object tracking the state of Pyodide executions by task ID */
        pyodideExecutions,
        /** Rebuild conversation messages with DA-specific metadata, task wiring, and replay semantics */
        rebuildConversationMessages,
        /** Reset DA-specific conversation state when starting a new chat */
        resetConversationState,
        /** Whether the Pyodide runner is currently executing a task, used to disable UI interactions during execution */
        pyodideRunnerRunning,
        /** Mapping from Pyodide task IDs to their corresponding chat messages for routing updates */
        pyodideTaskToMessage,
        /** Array of currently selected dataset IDs */
        selectedDatasets,
        /** Current selected datasets formatted for use in `FormData` component */
        selectedDatasetsFormData,
        /** Update message outputs based on artifacts */
        updateMessageOutputsFromArtifacts,
    };
}
