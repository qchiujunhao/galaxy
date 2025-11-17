<script setup lang="ts">
import { library } from "@fortawesome/fontawesome-svg-core";
import {
    faClock,
    faHistory,
    faMagic,
    faPaperPlane,
    faThumbsDown,
    faThumbsUp,
    faTrash,
    faUser,
} from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/vue-fontawesome";
import { BSkeleton } from "bootstrap-vue";
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";

import { GalaxyApi } from "@/api";
import { getAppRoot } from "@/onload/loadConfig";
import { type ActionSuggestion, type AgentResponse, useAgentActions } from "@/composables/agentActions";
import { useMarkdown } from "@/composables/markdown";
import { usePyodideRunner, type PyodideArtifact, type PyodideRunResult, type PyodideTask } from "@/composables/usePyodideRunner";
import { useToast } from "@/composables/toast";
import { errorMessageAsString } from "@/utils/simple-error";

import ActionCard from "./ChatGXY/ActionCard.vue";
import LoadingSpan from "@/components/LoadingSpan.vue";

library.add(faThumbsUp, faThumbsDown, faPaperPlane, faUser, faMagic, faHistory, faTrash, faClock);

interface AnalysisStep {
    type: 'thought' | 'action' | 'observation' | 'conclusion';
    content: string;
    requirements?: string[];
    status?: 'pending' | 'running' | 'completed' | 'error';
    stdout?: string;
    stderr?: string;
    success?: boolean;
}

interface Message {
    id: string;
    role: "user" | "assistant";
    content: string;
    timestamp: Date;
    agentType?: string;
    confidence?: string;
    feedback?: "up" | "down" | null;
    agentResponse?: AgentResponse;
    suggestions?: ActionSuggestion[];
    isSystemMessage?: boolean; // Flag for welcome/placeholder messages that shouldn't have feedback
    routingInfo?: {
        selected_agent: string;
        reasoning: string;
    };
    analysisSteps?: AnalysisStep[];
    artifacts?: UploadedArtifact[];
    generatedPlots?: string[];
    generatedFiles?: string[];
}

interface ChatHistoryItem {
    id: number;
    query: string;
    response: string;
    agent_type: string;
    agent_response?: AgentResponse; // Full agent response with suggestions
    timestamp: string;
    feedback?: number | null;
}

interface UploadedArtifact {
    dataset_id: string;
    name?: string;
    size?: number;
    mime_type?: string;
    download_url: string;
    history_id?: string;
}

interface ExecutionState {
    status: "pending" | "initialising" | "installing" | "fetching" | "running" | "submitting" | "completed" | "error";
    stdout: string;
    stderr: string;
    artifacts: UploadedArtifact[];
    errorMessage?: string;
}

const query = ref("");
const messages = ref<Message[]>([]);
const errorMessage = ref("");
const busy = ref(false);
const chatContainer = ref<HTMLElement>();
const selectedAgentType = ref("auto");
const showHistory = ref(false);
const chatHistory = ref<ChatHistoryItem[]>([]);
const loadingHistory = ref(false);
const currentChatId = ref<number | null>(null);
const hasLoadedInitialChat = ref(false);

const datasetOptions = ref<DatasetOption[]>([]);
const selectedDatasets = ref<string[]>([]);
const loadingDatasets = ref(false);
const datasetError = ref("");

const selectedDatasetRecords = computed(() =>
    datasetOptions.value.filter((dataset) => selectedDatasets.value.includes(dataset.id))
);

const toast = useToast();
const pyodideRunner = usePyodideRunner();
const pyodideExecutions = reactive<Record<string, ExecutionState>>({});
const chatStream = ref<WebSocket | null>(null);
const streamSupported = typeof window !== "undefined" && typeof WebSocket !== "undefined";
const deliveredTaskIds = new Set<string>();
const pyodideTaskToMessage = new Map<string, Message>();

const { renderMarkdown } = useMarkdown({ openLinksInNewPage: true, removeNewlinesAfterList: true });
const { processingAction, handleAction } = useAgentActions();

const agentTypes = [
    { value: "auto", label: "🧙 Auto (Router)", description: "Let the wizard decide" },
    { value: "error_analysis", label: "🔍 Error Analysis", description: "Debug tool errors" },
    { value: "tool_recommendation", label: "🔧 Tool Recommendation", description: "Find the right tools" },
    { value: "dspy_tool_recommendation", label: "🤖 DSPy Tools", description: "Advanced reasoning for tool selection" },
    { value: "custom_tool", label: "⚡ Custom Tool", description: "Create custom tools" },
    { value: "data_analysis", label: "🧪 Data Analysis", description: "Explore datasets with generated code" },
    // { value: "data_analysis_dspy", label: "📊 Data Analysis (DSPy)", description: "Iterative planning with DSPy + auto code execution" },
    { value: "gtn_training", label: "📚 Training Materials", description: "Find tutorials and guides" },
];

onMounted(async () => {
    await loadDatasetOptions();
    // Try to load the most recent chat
    await loadLatestChat();

    // If no chat was loaded, show the welcome message
    if (!hasLoadedInitialChat.value) {
        messages.value.push({
            id: generateId(),
            role: "assistant",
            content:
                "👋 Welcome to ChatGXY! I can help you with:\n\n" +
                "• **Finding the right tools** for your analysis\n" +
                "• **Debugging errors** in your workflows\n" +
                "• **Optimizing performance** of your pipelines\n" +
                "• **Checking data quality** issues\n\n" +
                "Just ask me anything about Galaxy and I'll route your question to the right specialist!",
            timestamp: new Date(),
            agentType: "router",
            confidence: "high",
            feedback: null,
            isSystemMessage: true,
        });
    }
});

function generateId() {
    return `msg-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
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



function getLatestUserQuery(): string {
    for (let i = messages.value.length - 1; i >= 0; i -= 1) {
        const entry = messages.value[i];
        if (entry.role === 'user') {
            return entry.content;
        }
    }
    return '';
}

function isLatestAssistantMessage(message: Message): boolean {
    if (message.role !== "assistant") {
        return false;
    }
    for (let i = messages.value.length - 1; i >= 0; i -= 1) {
        const candidate = messages.value[i];
        if (candidate.role === "assistant" && !candidate.isSystemMessage) {
            return candidate.id === message.id;
        }
    }
    return false;
}


function findMessageForPayload(payload: any): Message | undefined {
    const candidateTaskId =
        payload?.task_id ||
        payload?.pyodide_task_id ||
        payload?.agent_response?.metadata?.executed_task?.task_id ||
        payload?.agent_response?.metadata?.pyodide_task?.task_id;
    if (candidateTaskId && pyodideTaskToMessage.has(String(candidateTaskId))) {
        return pyodideTaskToMessage.get(String(candidateTaskId));
    }
    return undefined;
}

function populateAssistantMessage(
    target: Message,
    payload: any,
    fallbackAgentType: string,
    options?: { skipDatasetUpdate?: boolean }
) {
    const agentResponse = (payload?.agent_response ?? payload?.response?.agent_response) as AgentResponse | undefined;
    const content =
        typeof payload === "string"
            ? payload
            : payload?.response ?? agentResponse?.content ?? "No response received";
    const effectiveAgentType = agentResponse?.agent_type || (fallbackAgentType === "auto" ? "router" : fallbackAgentType);

    target.content = content;
    target.timestamp = payload?.timestamp ? new Date(payload.timestamp) : new Date();
    target.agentType = effectiveAgentType;
    target.confidence = agentResponse?.confidence || (payload?.confidence as string) || "medium";
    target.feedback = target.feedback ?? null;
    target.agentResponse = agentResponse;
    target.suggestions = agentResponse?.suggestions || [];
    target.routingInfo = payload?.routing_info;

    const metadata = agentResponse?.metadata as Record<string, unknown> | undefined;
    if (metadata) {
        const steps = normaliseAnalysisSteps((metadata as any)?.analysis_steps);
        if (steps.length) {
            target.analysisSteps = steps;
        }
        const artifactSource = (metadata as any)?.artifacts ?? (metadata as any)?.execution?.artifacts;
        const storedArtifacts = normaliseArtifactList(artifactSource);
        updateMessageOutputsFromArtifacts(target, storedArtifacts);
        const pyodideStatus = (metadata as any)?.pyodide_status;
        const shouldShowOutputs = storedArtifacts.length > 0 || pyodideStatus === "completed";
        if (shouldShowOutputs) {
            const plotEntries = normalisePathList((metadata as any)?.plots);
            target.generatedPlots = plotEntries.length ? plotEntries : undefined;
            const fileEntries = normalisePathList((metadata as any)?.files);
            target.generatedFiles = fileEntries.length ? fileEntries : undefined;
        } else {
            target.generatedPlots = undefined;
            target.generatedFiles = undefined;
        }
        const executedTask = (metadata as any)?.executed_task;
        if (executedTask?.task_id) {
            const taskId = String(executedTask.task_id);
            deliveredTaskIds.add(taskId);
            pyodideTaskToMessage.set(taskId, target);
        }
        const pendingTask = (metadata as any)?.pyodide_task;
        if (pendingTask?.task_id) {
            pyodideTaskToMessage.set(String(pendingTask.task_id), target);
        }
    }

    if (!options?.skipDatasetUpdate) {
        if (Array.isArray(payload?.dataset_ids) && payload.dataset_ids.length > 0) {
            selectedDatasets.value = payload.dataset_ids.map(String);
        }
    }
}

function appendAssistantMessage(payload: any, fallbackAgentType: string): Message {
    const existingMessage = findMessageForPayload(payload);
    if (existingMessage) {
        populateAssistantMessage(existingMessage, payload, fallbackAgentType, { skipDatasetUpdate: true });
        return existingMessage;
    }

    const assistantMessage: Message = {
        id: generateId(),
        role: "assistant",
        content: "",
        timestamp: new Date(),
        agentType: fallbackAgentType === "auto" ? "router" : fallbackAgentType,
        confidence: "medium",
        feedback: null,
    };

    populateAssistantMessage(assistantMessage, payload, fallbackAgentType);

    messages.value.push(assistantMessage);

    if (payload?.exchange_id) {
        currentChatId.value = payload.exchange_id;
    }

    maybeRunPyodideForMessage(assistantMessage);

    return assistantMessage;
}

function maybeRunPyodideForMessage(message: Message) {
    const metadata = message.agentResponse?.metadata as Record<string, any> | undefined;
    if (!metadata) {
        return;
    }
    const task = metadata.pyodide_task as PyodideTask | undefined;
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
        // Do not retry if we have already completed or errored out.
        if (existing.status === "completed" || existing.status === "error") {
            metadata.pyodide_status = existing.status;
            return;
        }
        return;
    }

    const status = metadata.pyodide_status as string | undefined;
    if (status === "error" || status === "completed") {
        pyodideExecutions[taskKey] = {
            status,
            stdout: metadata.stdout || "",
            stderr: metadata.stderr || "",
            artifacts: [],
            errorMessage: status === "error" ? metadata.error || "" : undefined,
        } as ExecutionState;
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
        toast.warning("This browser cannot run the generated analysis code.");
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

function runPyodideTaskForMessage(
    message: Message,
    task: PyodideTask,
    taskKey: string,
    metadata: Record<string, any>
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

    pyodideRunner
        .runTask(runnerTask, {
            onStdout: (line) => {
                state.stdout += line;
            },
            onStderr: (line) => {
                state.stderr += line;
            },
            onStatus: (event) => {
                state.status = mapStatus(event.status);
            },
        })
        .then(async (result) => {
            state.stdout = result.stdout;
            state.stderr = result.stderr;
            state.status = "submitting";
            let uploadedArtifacts: UploadedArtifact[] = [];
            try {
                uploadedArtifacts = await uploadArtifacts(result.artifacts || []);
                state.artifacts = uploadedArtifacts;
                updateMessageOutputsFromArtifacts(message, uploadedArtifacts);
                await submitPyodideExecutionResult(runnerTask, message, result, uploadedArtifacts);
                state.status = result.success ? "completed" : "error";
                if (!result.success && result.error) {
                    state.errorMessage = result.error;
                }
                metadata.pyodide_status = state.status;
            } catch (error) {
                const errMessage = error instanceof Error ? error.message : String(error);
                state.status = "error";
                state.errorMessage = errMessage;
                metadata.pyodide_status = "error";
                toast.error(`Pyodide execution failed: ${errMessage}`);
            }
        })
        .catch((error) => {
            const errMessage = error instanceof Error ? error.message : String(error);
            if (error && (error as any).stdout && typeof (error as any).stdout === "string") {
                state.stdout += (error as any).stdout;
            }
            if (error && (error as any).stderr && typeof (error as any).stderr === "string") {
                state.stderr += (error as any).stderr;
            }
            state.status = "error";
            state.errorMessage = errMessage;
            metadata.pyodide_status = "error";
            toast.error(`Pyodide execution failed: ${errMessage}`);
        });
}
function openChatStream(exchangeId: number) {
    if (!streamSupported) {
        return;
    }
    const existing = chatStream.value;
    if (existing) {
        const existingId = (existing as any)._exchangeId as number | undefined;
        if (existingId === exchangeId &&
            (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING)) {
            return;
        }
        existing.close();
    }

    try {
        const appRoot = getAppRoot(undefined, true) || "/";
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
                    populateAssistantMessage(existing, payload.payload, selectedAgentType.value, { skipDatasetUpdate: true });
                    return;
                }
            }
            appendAssistantMessage(payload.payload, selectedAgentType.value);
        }
    } catch (error) {
        console.error("Failed to process chat stream message", error);
    }
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
        const formData = new FormData();
        formData.append("file", blob, artifact.name || "artifact");
        if (artifact.name) {
            formData.append("name", artifact.name);
        }
        formData.append("mime_type", artifact.mime_type || blob.type || "application/octet-stream");
        formData.append("size", String(blob.size));

        const response = await fetch(`${getAppRoot()}api/chat/exchange/${currentChatId.value}/artifacts`, {
            method: "POST",
            body: formData,
            credentials: "include",
        });

        if (!response.ok) {
            const errorText = await response.text();
            const description = errorText || response.statusText || "Unknown error";
            throw new Error(`Artifact upload failed (${response.status}): ${description}`);
        }

        const payload = (await response.json()) as UploadedArtifact;
        if (payload.download_url) {
            payload.download_url = resolveDownloadUrl(payload.download_url);
        }
        results.push(payload);
        artifact.buffer = undefined;
    }
    return results;
}

function resolveDownloadUrl(url: string): string {
    if (!url) {
        return url;
    }
    if (/^https?:\/\//i.test(url)) {
        return url;
    }
    const rootCandidate = getAppRoot(undefined, true) || window.location.origin;
    const absoluteRoot = rootCandidate.replace(/\/$/, "");
    if (url.startsWith("/")) {
        return `${absoluteRoot}${url}`;
    }
    return `${getAppRoot()}${url}`;
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
    message: Message,
    result: PyodideRunResult,
    artifacts: UploadedArtifact[]
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

    const { data, error } = await GalaxyApi().POST(`/api/chat/exchange/${currentChatId.value}/pyodide_result`, {
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

function pyodideStateForMessage(message: Message): ExecutionState | undefined {
    const metadata = message.agentResponse?.metadata as Record<string, any> | undefined;
    if (!metadata?.pyodide_task) {
        return undefined;
    }
    const task = metadata.pyodide_task as PyodideTask;
    const key = task.task_id || message.id;
    return pyodideExecutions[key];
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
        if (typeof newId === "number") {
            openChatStream(newId);
        }
        if (newId == null) {
            deliveredTaskIds.clear();
        }
    },
    { immediate: false },
);
onBeforeUnmount(() => {
    closeChatStream();
});




function downloadArtifact(artifact: UploadedArtifact) {
    if (artifact.download_url) {
        window.open(artifact.download_url, "_blank");
        return;
    }
    toast.info("Artifact download is not available yet.");
}

function formatSize(size?: number): string {
    if (size === undefined || size === null) {
        return "";
    }
    if (size < 1024) {
        return `${size} B`;
    }
    const units = ["KB", "MB", "GB", "TB"];
    let value = size / 1024;
    let unitIndex = 0;
    while (value >= 1024 && unitIndex < units.length - 1) {
        value /= 1024;
        unitIndex += 1;
    }
    return `${value.toFixed(1)} ${units[unitIndex]}`;
}

function normalisePathList(raw: unknown): string[] {
    if (!Array.isArray(raw)) {
        return [];
    }
    const results: string[] = [];
    const seen = new Set<string>();
    for (const entry of raw) {
        const text = String(entry ?? "").trim();
        if (!text) {
            continue;
        }
        const normalised = text.startsWith("generated_file")
            ? text
            : `generated_file/${text.replace(/^\/+/, "")}`;
        if (!seen.has(normalised)) {
            seen.add(normalised);
            results.push(normalised);
        }
    }
    return results;
}

function normaliseArtifactList(raw: unknown): UploadedArtifact[] {
    if (!Array.isArray(raw)) {
        return [];
    }
    const artifacts: UploadedArtifact[] = [];
    for (const entry of raw) {
        if (!entry || typeof entry !== "object") {
            continue;
        }
        const record = entry as Record<string, any>;
        const identifier =
            record.dataset_id || record.name || record.path || record.id || generateId();
        const downloadUrl = record.download_url ? resolveDownloadUrl(String(record.download_url)) : undefined;
        artifacts.push({
            dataset_id: String(identifier),
            name: record.name ? String(record.name) : undefined,
            size: typeof record.size === "number" ? record.size : Number(record.size) || undefined,
            mime_type: record.mime_type ? String(record.mime_type) : undefined,
            download_url: downloadUrl || "",
            history_id: record.history_id ? String(record.history_id) : undefined,
        });
    }
    return artifacts;
}

function normaliseGeneratedEntry(entry: string): string {
    return entry.replace(/^generated_file\//, "").replace(/^\/+/, "");
}

function findArtifactForEntry(entry: string, artifacts?: UploadedArtifact[]): UploadedArtifact | undefined {
    if (!artifacts || artifacts.length === 0) {
        return undefined;
    }
    const normalized = normaliseGeneratedEntry(entry);
    return artifacts.find((artifact) => {
        const artifactName = normaliseGeneratedEntry(artifact.name || "");
        return (
            artifactName === normalized ||
            artifactName === entry ||
            artifact.dataset_id === normalized ||
            artifact.dataset_id === entry
        );
    });
}

function artifactPreviewUrl(entry: string, artifacts?: UploadedArtifact[]): string | undefined {
    const match = findArtifactForEntry(entry, artifacts);
    return match?.download_url || undefined;
}

function artifactDownloadHandler(entry: string, artifacts?: UploadedArtifact[]) {
    const match = findArtifactForEntry(entry, artifacts);
    if (match) {
        downloadArtifact(match);
    }
}

function formatGeneratedEntry(entry: string): string {
    return entry.replace(/^generated_file\//, "");
}

function artifactIsDownloadable(entry: string, artifacts?: UploadedArtifact[]): boolean {
    return Boolean(findArtifactForEntry(entry, artifacts));
}

function updateMessageOutputsFromArtifacts(message: Message, artifacts: UploadedArtifact[] | undefined) {
    if (!artifacts || artifacts.length === 0) {
        return;
    }
    message.artifacts = artifacts;
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
    message.generatedPlots = plotNames.length ? plotNames : message.generatedPlots;
    message.generatedFiles = fileNames.length ? fileNames : message.generatedFiles;
}


function normaliseAnalysisSteps(raw: unknown): AnalysisStep[] {
    if (!Array.isArray(raw)) {
        return [];
    }

    return raw
        .map((step) => {
            if (!step || typeof step !== 'object') {
                return null;
            }
            const record = step as Record<string, unknown>;
            const type = record.type;
            if (type !== 'thought' && type !== 'action' && type !== 'observation' && type !== 'conclusion') {
                return null;
            }
            const content = String(record.content ?? '');
            const requirements = Array.isArray(record.requirements)
                ? (record.requirements as unknown[]).map(String)
                : undefined;
            const statusValue = record.status;
            const status: AnalysisStep['status'] | undefined =
                statusValue === 'running' || statusValue === 'completed' || statusValue === 'error'
                    ? statusValue
                    : undefined;
            const stdout = typeof record.stdout === 'string' ? record.stdout : undefined;
            const stderr = typeof record.stderr === 'string' ? record.stderr : undefined;
            const success = typeof record.success === 'boolean' ? record.success : undefined;
            return {
                type,
                content,
                requirements,
                status: type === 'action' ? status ?? 'pending' : undefined,
                stdout,
                stderr,
                success,
            } as AnalysisStep;
        })
        .filter((step): step is AnalysisStep => Boolean(step));
}

async function loadDatasetOptions() {
    loadingDatasets.value = true;
    datasetError.value = "";

    try {
        const { data, error } = await GalaxyApi().GET("/api/datasets", {
            params: {
                query: {
                    limit: 200,
                    order: "update_time-dsc",
                },
            },
        });

        if (error) {
            datasetError.value = errorMessageAsString(error, "Failed to load datasets");
            datasetOptions.value = [];
            return;
        }

        if (Array.isArray(data)) {
            datasetOptions.value = data
                .map((item: any) => {
                    const id = String(item.id || item.dataset_id || item.hda_id || "");
                    const name = item.name || `Dataset ${item.hid || ""}`;
                    const extension = item.extension || item.ext || item.file_ext || undefined;
                    const sizeValue = item.file_size_bytes ?? item.file_size ?? item.size ?? undefined;
                    const size = typeof sizeValue === "number" ? sizeValue : Number(sizeValue ?? 0);
                    return id ? { id, name, extension, size: Number.isFinite(size) ? size : undefined } : null;
                })
                .filter((entry): entry is DatasetOption => Boolean(entry));
        }
    } catch (e) {
        datasetError.value = errorMessageAsString(e, "Failed to load datasets");
        datasetOptions.value = [];
    } finally {
        loadingDatasets.value = false;
    }
}

async function submitQuery() {
    if (!query.value.trim()) {
        return;
    }

    const userMessage: Message = {
        id: generateId(),
        role: "user",
        content: query.value,
        timestamp: new Date(),
        feedback: null,
    };

    messages.value.push(userMessage);
    const currentQuery = query.value;
    query.value = "";

    // Scroll to bottom after adding user message
    await nextTick();
    scrollToBottom();

    busy.value = true;
    errorMessage.value = "";

    try {
        const { data, error } = await GalaxyApi().POST("/api/chat", {
            params: {
                query: {
                    agent_type: selectedAgentType.value,
                },
            },
            body: {
                job_id: null,
                query: currentQuery,
                agent_type: selectedAgentType.value,
                exchange_id: currentChatId.value, // Backend will load full conversation history
                dataset_ids: selectedDatasets.value,
            } as any,
        });

        if (error) {
            errorMessage.value = errorMessageAsString(error, "Failed to get response from ChatGXY.");
            const errorMsg: Message = {
                id: generateId(),
                role: "assistant",
                content: `❌ Error: ${errorMessage.value}`,
                timestamp: new Date(),
                agentType: selectedAgentType.value,
                confidence: "low",
                feedback: null,
            };
            messages.value.push(errorMsg);

            // Scroll to bottom after adding error message
            await nextTick();
            scrollToBottom();
        } else if (data) {
            const fallbackAgent = selectedAgentType.value === "auto" ? "router" : selectedAgentType.value;
            appendAssistantMessage(data, fallbackAgent);

            await nextTick();
            scrollToBottom();
        }
    } catch (e) {
        errorMessage.value = `Unexpected error: ${e}`;
        const errorMsg: Message = {
            id: generateId(),
            role: "assistant",
            content: `❌ Unexpected error occurred. Please try again.`,
            timestamp: new Date(),
            agentType: selectedAgentType.value,
            confidence: "low",
            feedback: null,
        };
        messages.value.push(errorMsg);

        // Scroll to bottom after adding error message
        await nextTick();
        scrollToBottom();
    } finally {
        busy.value = false;
        await nextTick();
        scrollToBottom();
    }
}

function scrollToBottom() {
    if (chatContainer.value) {
        // Use smooth scrolling and avoid focus disruption
        chatContainer.value.scrollTo({
            top: chatContainer.value.scrollHeight,
            behavior: 'auto' // Use 'smooth' if you want animated scrolling
        });
    }
}

async function sendFeedback(messageId: string, value: "up" | "down") {
    const message = messages.value.find((m) => m.id === messageId);
    if (message) {
        // Update UI immediately
        message.feedback = value;

        // Only persist if we have a currentChatId (for saved chats)
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
                    // Revert on error
                    message.feedback = null;
                }
            } catch (e) {
                console.error("Failed to save feedback:", e);
                // Revert on error
                message.feedback = null;
            }
        }
    }
}

function getAgentIcon(agentType?: string) {
    switch (agentType) {
        case "router":
            return "🧙";
        case "error_analysis":
            return "🔍";
        case "tool_recommendation":
            return "🔧";
        case "dspy_tool_recommendation":
            return "🤖";
        case "custom_tool":
            return "⚡";
        case "data_analysis":
            return "📊";
        case "gtn_training":
            return "📚";
        default:
            return "🤖";
    }
}

function getAgentLabel(agentType?: string) {
    const agent = agentTypes.find((a) => a.value === agentType);
    return agent?.label.split(" ").slice(1).join(" ") || "AI Assistant";
}

function getAgentDescription(agentType?: string) {
    const descriptions = {
        "router": "Intelligent query routing and task classification",
        "error_analysis": "Debugging tool errors and job failures",
        "tool_recommendation": "Finding the right Galaxy tools for your analysis",
        "dspy_tool_recommendation": "Advanced reasoning for tool selection using DSPy",
        "custom_tool": "Creating custom Galaxy tools and wrappers", 
        "data_analysis": "Exploratory analysis and code-driven insights",
        "gtn_training": "Finding tutorials and training materials"
    };
    return descriptions[agentType as keyof typeof descriptions] || "General AI assistance";
}

async function loadChatHistory() {
    loadingHistory.value = true;
    try {
        const { data, error } = await GalaxyApi().GET("/api/chat/history", {
            params: {
                query: { limit: 50 },
            },
        });

        if (data && !error) {
            chatHistory.value = data as ChatHistoryItem[];
        }
    } catch (e) {
        console.error("Failed to load chat history:", e);
    } finally {
        loadingHistory.value = false;
    }
}

async function clearHistory() {
    if (!confirm("Are you sure you want to clear your chat history?")) {
        return;
    }

    try {
        const { data, error } = await GalaxyApi().DELETE("/api/chat/history");
        if (!error && data) {
            console.log("Clear history response:", data);
            chatHistory.value = [];
            // Also clear current chat if it was from history
            if (currentChatId.value) {
                startNewChat();
            }
        } else if (error) {
            console.error("Failed to clear history - API error:", error);
            alert("Failed to clear history. Please try again.");
        }
    } catch (e) {
        console.error("Failed to clear history - exception:", e);
        alert("Failed to clear history. Please try again.");
    }
}

async function loadPreviousChat(item: ChatHistoryItem) {
    // Try to load the full conversation from the backend
    try {
        const { data: fullConversation } = await GalaxyApi().GET(`/api/chat/exchange/{exchange_id}/messages`, {
            params: {
                path: {
                    exchange_id: item.id,
                },
            },
        });

        if (fullConversation && fullConversation.length > 0) {
            // Clear and rebuild messages from full conversation
            messages.value = [];
            deliveredTaskIds.clear();
            pyodideTaskToMessage.clear();

            fullConversation.forEach((msg: any, index: number) => {
                if (msg.role === "execution_result") {
                    if (msg.task_id) {
                        deliveredTaskIds.add(String(msg.task_id));
                    }
                    return;
                }
                const message: Message = {
                    id: `hist-${msg.role}-${item.id}-${index}`,
                    role: msg.role as "user" | "assistant",
                    content: msg.content,
                    timestamp: msg.timestamp ? new Date(msg.timestamp) : new Date(),
                    feedback: null,
                };

                if (msg.role === "assistant") {
                    message.agentType = msg.agent_type;
                    message.confidence = msg.agent_response?.confidence || "medium";
                    message.feedback = msg.feedback === 1 ? "up" : msg.feedback === 0 ? "down" : null;

                    if (msg.agent_response) {
                        message.agentResponse = msg.agent_response;
                        message.suggestions = msg.agent_response.suggestions || [];
                        const metadata = (msg.agent_response as any)?.metadata as Record<string, unknown> | undefined;
                        const steps = metadata ? normaliseAnalysisSteps((metadata as any)?.analysis_steps) : [];
                        if (steps.length) {
                            message.analysisSteps = steps;
                        }
                        if (metadata) {
                            const artifactSource = (metadata as any)?.artifacts ?? (metadata as any)?.execution?.artifacts;
                            const storedArtifacts = normaliseArtifactList(artifactSource);
                            updateMessageOutputsFromArtifacts(message, storedArtifacts);
                            const pyodideStatus = (metadata as any)?.pyodide_status;
                            const shouldShowOutputs = storedArtifacts.length > 0 || pyodideStatus === "completed";
                            if (shouldShowOutputs) {
                                const plots = normalisePathList((metadata as any)?.plots);
                                message.generatedPlots = plots.length ? plots : undefined;
                                const files = normalisePathList((metadata as any)?.files);
                                message.generatedFiles = files.length ? files : undefined;
                            } else {
                                message.generatedPlots = undefined;
                                message.generatedFiles = undefined;
                            }
                            const executedTask = (metadata as any)?.executed_task;
                            if (executedTask?.task_id) {
                                deliveredTaskIds.add(String(executedTask.task_id));
                                pyodideTaskToMessage.set(String(executedTask.task_id), message);
                            }
                            const pendingTask = (metadata as any)?.pyodide_task;
                            if (pendingTask?.task_id) {
                                pyodideTaskToMessage.set(String(pendingTask.task_id), message);
                            }
                        }
                    }
                }

                messages.value.push(message);

                if (msg.role === "assistant") {
                    maybeRunPyodideForMessage(message);
                }
            });

            applyDatasetSelectionFromMessages(fullConversation);
        } else {
            // Fallback to single message if no full conversation available
            loadSingleMessageFallback(item);
        }

        currentChatId.value = item.id;
        showHistory.value = false;
        nextTick(() => scrollToBottom());
    } catch (error) {
        console.error("Error loading full conversation:", error);
        // Fallback to simple loading on error
        loadSingleMessageFallback(item);
    }
}

function loadSingleMessageFallback(item: ChatHistoryItem) {
    // Fallback method for loading just the first message pair
    const userMessage: Message = {
        id: `hist-user-${item.id}`,
        role: "user",
        content: item.query,
        timestamp: new Date(item.timestamp),
        feedback: null,
    };

    const assistantMessage: Message = {
        id: `hist-assistant-${item.id}`,
        role: "assistant",
        content: item.response,
        timestamp: new Date(item.timestamp),
        agentType: item.agent_type,
        confidence: item.agent_response?.confidence || "medium",
        feedback: item.feedback === 1 ? "up" : item.feedback === 0 ? "down" : null,
    };

    if (item.agent_response) {
        assistantMessage.agentResponse = item.agent_response;
        assistantMessage.suggestions = item.agent_response.suggestions || [];
        const metadata = (item.agent_response as any)?.metadata as Record<string, unknown> | undefined;
        const steps = metadata ? normaliseAnalysisSteps((metadata as any)?.analysis_steps) : [];
        if (steps.length) {
            assistantMessage.analysisSteps = steps;
        }
        if (metadata) {
            const artifactSource = (metadata as any)?.artifacts ?? (metadata as any)?.execution?.artifacts;
            const storedArtifacts = normaliseArtifactList(artifactSource);
            updateMessageOutputsFromArtifacts(assistantMessage, storedArtifacts);
            const pyodideStatus = (metadata as any)?.pyodide_status;
            const shouldShowOutputs = storedArtifacts.length > 0 || pyodideStatus === "completed";
            if (shouldShowOutputs) {
                const plots = normalisePathList((metadata as any)?.plots);
                assistantMessage.generatedPlots = plots.length ? plots : undefined;
                const files = normalisePathList((metadata as any)?.files);
                assistantMessage.generatedFiles = files.length ? files : undefined;
            } else {
                assistantMessage.generatedPlots = undefined;
                assistantMessage.generatedFiles = undefined;
            }
            const executedTask = (metadata as any)?.executed_task;
            if (executedTask?.task_id) {
                deliveredTaskIds.add(String(executedTask.task_id));
                pyodideTaskToMessage.set(String(executedTask.task_id), assistantMessage);
            }
            const pendingTask = (metadata as any)?.pyodide_task;
            if (pendingTask?.task_id) {
                pyodideTaskToMessage.set(String(pendingTask.task_id), assistantMessage);
            }
        }
    }

    messages.value = [userMessage, assistantMessage];
    currentChatId.value = item.id;
    showHistory.value = false;
    nextTick(() => scrollToBottom());

    maybeRunPyodideForMessage(assistantMessage);

    const metadataDatasets = (item.agent_response as any)?.metadata?.datasets_used;
    if (Array.isArray(metadataDatasets) && metadataDatasets.length > 0) {
        selectedDatasets.value = metadataDatasets.map(String);
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
            const latestChat = data[0] as ChatHistoryItem;
            await loadPreviousChat(latestChat);
            hasLoadedInitialChat.value = true;
        }
    } catch (e) {
        console.error("Failed to load latest chat:", e);
    }
}

function startNewChat() {
    // Clear messages and reset to welcome message
    messages.value = [
        {
            id: generateId(),
            role: "assistant",
            content: "👋 Starting a new conversation! How can I help you today?",
            timestamp: new Date(),
            agentType: "router",
            confidence: "high",
            feedback: null,
            isSystemMessage: true,
        },
    ];
    Object.keys(pyodideExecutions).forEach((key) => delete pyodideExecutions[key]);
    currentChatId.value = null;
    deliveredTaskIds.clear();
    pyodideTaskToMessage.clear();
    selectedDatasets.value = [];
    query.value = "";
    errorMessage.value = "";
}

function toggleHistory() {
    showHistory.value = !showHistory.value;
    if (showHistory.value && chatHistory.value.length === 0) {
        loadChatHistory();
    }
}

// Format timestamp for display
function formatTime(timestamp: string) {
    const date = new Date(timestamp);
    const now = new Date();
    const diff = now.getTime() - date.getTime();
    const hours = Math.floor(diff / (1000 * 60 * 60));

    if (hours < 1) {
        const minutes = Math.floor(diff / (1000 * 60));
        return `${minutes}m ago`;
    } else if (hours < 24) {
        return `${hours}h ago`;
    } else {
        const days = Math.floor(hours / 24);
        return `${days}d ago`;
    }
}
</script>

<template>
    <div class="chatgxy-container card">
        <div class="card-header">
            <div class="d-flex align-items-center justify-content-between">
                <h3 class="mb-0 d-flex align-items-center">
                    <FontAwesomeIcon :icon="faMagic" fixed-width />
                    ChatGXY
                    <span v-if="currentChatId" class="badge badge-info ml-2" style="font-size: 0.6em">
                        Continuing Chat #{{ currentChatId }}
                    </span>
                </h3>
                <div class="d-flex align-items-center">
                    <button class="btn btn-sm btn-primary mr-2" title="Start New Chat" @click="startNewChat">
                        <FontAwesomeIcon :icon="faPaperPlane" fixed-width />
                        New Chat
                    </button>
                    <button
                        class="btn btn-sm btn-outline-secondary mr-3"
                        :title="showHistory ? 'Hide History' : 'Show History'"
                        @click="toggleHistory">
                        <FontAwesomeIcon :icon="faHistory" fixed-width />
                        History
                    </button>
                    <div class="agent-selector">
                        <label for="agent-select" class="mr-2">Agent:</label>
                        <select id="agent-select" v-model="selectedAgentType" class="form-control form-control-sm">
                            <option v-for="agent in agentTypes" :key="agent.value" :value="agent.value">
                                {{ agent.label }}
                            </option>
                        </select>
                    </div>
                </div>
            </div>
        </div>

        <div class="card-body d-flex">
            <!-- History Sidebar -->
            <div v-if="showHistory" class="history-sidebar">
                <div class="history-header">
                    <h5>Chat History</h5>
                    <button class="btn btn-sm btn-link text-danger p-0" title="Clear History" @click="clearHistory">
                        <FontAwesomeIcon :icon="faTrash" />
                    </button>
                </div>

                <div v-if="loadingHistory" class="text-center p-3">
                    <LoadingSpan message="Loading history..." />
                </div>

                <div v-else-if="chatHistory.length === 0" class="text-muted p-3 text-center">No chat history yet</div>

                <div v-else class="history-list">
                    <div
                        v-for="item in chatHistory"
                        :key="item.id"
                        class="history-item"
                        @click="() => loadPreviousChat(item)">
                        <div class="history-query">{{ item.query }}</div>
                        <div class="history-meta">
                            <span class="history-agent">{{ getAgentIcon(item.agent_type) }}</span>
                            <span class="history-time">
                                <FontAwesomeIcon :icon="faClock" class="mr-1" />
                                {{ formatTime(item.timestamp) }}
                            </span>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Main Chat Area -->
            <div ref="chatContainer" class="chat-messages flex-grow-1">
                <div class="dataset-selector-panel card mb-3">
                    <div class="card-body">
                        <div class="d-flex align-items-center justify-content-between mb-2">
                            <label for="dataset-select" class="mb-0">Datasets</label>
                            <small v-if="loadingDatasets" class="text-muted">Loading…</small>
                        </div>
                        <select
                            id="dataset-select"
                            v-model="selectedDatasets"
                            multiple
                            class="form-control"
                            :disabled="loadingDatasets || busy">
                            <option
                                v-for="dataset in datasetOptions"
                                :key="dataset.id"
                                :value="dataset.id">
                                {{ dataset.name }}
                            </option>
                        </select>
                        <div v-if="datasetError" class="text-danger small mt-2">{{ datasetError }}</div>
                        <div v-else class="selected-datasets mt-2" v-show="selectedDatasetRecords.length">
                            <span
                                v-for="dataset in selectedDatasetRecords"
                                :key="dataset.id"
                                class="badge badge-primary mr-1">
                                {{ dataset.name }}
                            </span>
                        </div>
                    </div>
                </div>

                <div
                    v-for="message in messages"
                    :key="message.id"
                    :class="['message', message.role === 'user' ? 'user-message' : 'assistant-message']">
                    <div class="message-header">
                        <span class="message-icon">
                            <FontAwesomeIcon v-if="message.role === 'user'" :icon="faUser" fixed-width />
                            <span v-else>{{ getAgentIcon(message.agentType) }}</span>
                        </span>
                        <div class="message-agent-info">
                            <span class="message-role">
                                {{ message.role === "user" ? "You" : getAgentLabel(message.agentType) }}
                            </span>
                            <span 
                                v-if="message.role === 'assistant' && message.agentType"
                                class="agent-description"
                                :title="getAgentDescription(message.agentType)">
                                {{ getAgentDescription(message.agentType) }}
                            </span>
                        </div>
                        <div class="message-badges">
                            <span
                                v-if="message.confidence"
                                class="confidence-badge"
                                :class="`confidence-${message.confidence}`"
                                :title="`Confidence: ${message.confidence}`">
                                {{ message.confidence }}
                            </span>
                            <span v-if="message.routingInfo" class="routing-info" :title="message.routingInfo.reasoning">
                                → {{ getAgentLabel(message.routingInfo.selected_agent) }}
                            </span>
                        </div>
                    </div>


<div class="message-content">
    <template v-if="message.role === 'assistant'">
        <!-- eslint-disable-next-line vue/no-v-html -->
        <div v-html="renderMarkdown(message.content)" />
        <div v-if="message.generatedPlots?.length || message.generatedFiles?.length" class="generated-output mt-2">
            <div v-if="message.generatedPlots?.length" class="mb-2">
                <h6 class="mb-1">Generated Plots</h6>
                <ul class="list-unstyled mb-0 small">
                    <li v-for="plot in message.generatedPlots" :key="`${message.id}-plot-${plot}`" class="mb-2">
                        <code>{{ formatGeneratedEntry(plot) }}</code>
                        <div v-if="artifactPreviewUrl(plot, message.artifacts)" class="mt-1">
                            <img
                                :src="artifactPreviewUrl(plot, message.artifacts)"
                                :alt="formatGeneratedEntry(plot)"
                                class="plot-preview img-thumbnail"
                            />
                        </div>
                        <button
                            v-if="artifactIsDownloadable(plot, message.artifacts)"
                            class="btn btn-link btn-sm p-0 mt-1"
                            type="button"
                            @click="artifactDownloadHandler(plot, message.artifacts)"
                        >
                            Download
                        </button>
                    </li>
                </ul>
            </div>
            <div v-if="message.generatedFiles?.length">
                <h6 class="mb-1">Generated Files</h6>
                <ul class="list-unstyled mb-0 small">
                    <li v-for="file in message.generatedFiles" :key="`${message.id}-file-${file}`">
                        <code>{{ formatGeneratedEntry(file) }}</code>
                        <button
                            v-if="artifactIsDownloadable(file, message.artifacts)"
                            class="btn btn-link btn-sm p-0 ml-2"
                            type="button"
                            @click="artifactDownloadHandler(file, message.artifacts)"
                        >
                            Download
                        </button>
                    </li>
                </ul>
            </div>
        </div>
    <div
        v-if="message.artifacts?.length"
        class="mt-2"
    >
        <h6 class="mb-1">Saved Artifacts</h6>
        <ul class="list-unstyled mb-0">
            <li v-for="artifact in message.artifacts" :key="artifact.dataset_id || artifact.name" class="mb-2">
                <button
                    v-if="artifact.download_url"
                    class="btn btn-link btn-sm"
                    type="button"
                    @click="downloadArtifact(artifact)"
                >
                    {{ artifact.name || artifact.dataset_id }}
                </button>
                <span v-else>{{ artifact.name || artifact.dataset_id }}</span>
                <span v-if="artifact.size" class="text-muted ml-1">({{ formatSize(artifact.size) }})</span>
                <div v-if="artifact.mime_type && artifact.mime_type.startsWith('image/') && artifact.download_url" class="mt-2">
                    <img
                        :src="artifact.download_url"
                        :alt="artifact.name || 'plot preview'"
                        class="plot-preview img-thumbnail"
                    />
                </div>
            </li>
        </ul>
    </div>
    <div v-if="message.agentResponse?.metadata?.executed_task?.code" class="mt-2 executed-code">
        <details open>
            <summary class="text-muted">Executed Python Code</summary>
            <pre>{{ message.agentResponse?.metadata?.executed_task?.code }}</pre>
        </details>
        <div v-if="message.agentResponse?.metadata?.stdout" class="mt-2">
            <details open>
                <summary class="text-muted">Execution Stdout</summary>
                <pre>{{ message.agentResponse?.metadata?.stdout }}</pre>
            </details>
        </div>
        <div v-if="message.agentResponse?.metadata?.stderr" class="mt-2">
            <details>
                <summary class="text-muted">Execution Stderr</summary>
                <pre class="text-danger">{{ message.agentResponse?.metadata?.stderr }}</pre>
            </details>
        </div>
    </div>
    </template>
    <div v-else>{{ message.content }}</div>
</div>

<div v-if="message.analysisSteps?.length" class="analysis-steps card mt-2">
    <div
        v-for="(step, idx) in message.analysisSteps"
        :key="idx"
        class="analysis-step"
        :class="[step.type, step.status && step.status !== 'pending' ? step.status : '']">
        <div class="analysis-step-header">
            <span class="step-label">
                {{ step.type === 'thought'
                    ? 'Plan'
                    : step.type === 'action'
                        ? 'Action'
                        : step.type === 'observation'
                            ? 'Observation'
                            : 'Conclusion' }}
            </span>
            <span
                v-if="step.type === 'action' && step.status && step.status !== 'pending'"
                class="step-status"
                :class="step.status">
                {{ step.status }}
            </span>
            <span
                v-else-if="step.type === 'observation' && step.success !== undefined"
                class="step-status"
                :class="step.success ? 'completed' : 'error'">
                {{ step.success ? 'success' : 'error' }}
            </span>
        </div>
        <div class="analysis-step-body">
            <pre v-if="step.type === 'action'">{{ step.content }}</pre>
            <div v-else-if="step.type === 'observation'">
                <div v-if="step.stdout">
                    <small class="text-muted">stdout</small>
                    <pre>{{ step.stdout }}</pre>
                </div>
                <div v-if="step.stderr">
                    <small class="text-muted">stderr</small>
                    <pre class="text-danger">{{ step.stderr }}</pre>
                </div>
                <div v-if="!step.stdout && !step.stderr">No textual output.</div>
            </div>
            <div v-else>{{ step.content }}</div>
            <div v-if="step.type === 'action' && step.requirements?.length" class="step-requirements">
                <small class="text-muted">requirements: {{ step.requirements.join(', ') }}</small>
            </div>
        </div>
    </div>
</div>

<div v-if="message.role === 'assistant' && pyodideStateForMessage(message)" class="pyodide-status card mt-2">
    <div class="card-body">
        <div v-if="pyodideStateForMessage(message)?.status === 'initialising'" class="text-muted">Preparing browser environment…</div>
        <div v-else-if="pyodideStateForMessage(message)?.status === 'installing'" class="text-muted">Installing Python packages…</div>
        <div v-else-if="pyodideStateForMessage(message)?.status === 'fetching'" class="text-muted">Downloading datasets…</div>
        <div v-else-if="pyodideStateForMessage(message)?.status === 'running'" class="text-muted">Running generated Python in the browser…</div>
        <div v-else-if="pyodideStateForMessage(message)?.status === 'submitting'" class="text-muted">Sending results back to Galaxy…</div>
        <div v-else-if="pyodideStateForMessage(message)?.status === 'completed'" class="text-success">Execution completed in your browser.</div>
        <div v-else-if="pyodideStateForMessage(message)?.status === 'error'" class="text-danger">
            Execution failed{{ pyodideStateForMessage(message)?.errorMessage ? ': ' + pyodideStateForMessage(message)?.errorMessage : '' }}
        </div>

        <div v-if="pyodideStateForMessage(message)?.stdout" class="mt-2">
            <h6 class="mb-1">Stdout</h6>
            <pre class="pyodide-stream">{{ pyodideStateForMessage(message)?.stdout }}</pre>
        </div>
        <div v-if="pyodideStateForMessage(message)?.stderr" class="mt-2">
            <h6 class="mb-1">Stderr</h6>
            <pre class="pyodide-stream text-danger">{{ pyodideStateForMessage(message)?.stderr }}</pre>
        </div>
        <div v-if="pyodideStateForMessage(message)?.artifacts.length" class="mt-2">
            <h6 class="mb-1">Artifacts</h6>
            <ul class="list-unstyled mb-0">
                <li
                    v-for="artifact in pyodideStateForMessage(message)?.artifacts"
                    :key="artifact.dataset_id || artifact.name"
                    class="mb-2"
                >
                    <button class="btn btn-link btn-sm" type="button" @click="downloadArtifact(artifact)">
                        {{ artifact.name || artifact.dataset_id }}
                    </button>
                    <span v-if="artifact.size" class="text-muted ml-1">({{ formatSize(artifact.size) }})</span>
                    <div v-if="artifact.mime_type && artifact.mime_type.startsWith('image/')" class="mt-2">
                        <img
                            :src="artifact.download_url"
                            :alt="artifact.name || 'plot preview'"
                            class="plot-preview img-thumbnail"
                        />
                    </div>
                </li>
            </ul>
        </div>
    </div>
</div>

<!-- Action suggestions for assistant messages -->
<ActionCard
                        v-if="isLatestAssistantMessage(message) && message.suggestions?.length"
                        :suggestions="message.suggestions"
                        :processing-action="processingAction"
                        @handle-action="(action) => handleAction(action, message.agentResponse || {})" />



                    <div
                        v-if="
                            message.role === 'assistant' &&
                            !message.content.startsWith('❌') &&
                            !message.isSystemMessage
                        "
                        class="message-feedback">
                        <button
                            class="btn btn-link btn-sm"
                            :disabled="message.feedback !== null"
                            :class="{ 'feedback-given': message.feedback === 'up' }"
                            @click="sendFeedback(message.id, 'up')">
                            <FontAwesomeIcon :icon="faThumbsUp" fixed-width />
                        </button>
                        <button
                            class="btn btn-link btn-sm"
                            :disabled="message.feedback !== null"
                            :class="{ 'feedback-given': message.feedback === 'down' }"
                            @click="sendFeedback(message.id, 'down')">
                            <FontAwesomeIcon :icon="faThumbsDown" fixed-width />
                        </button>
                        <span v-if="message.feedback" class="feedback-text">Thanks for feedback!</span>
                    </div>
                </div>

                <div v-if="busy" class="message assistant-message">
                    <div class="message-header">
                        <span class="message-icon">{{ getAgentIcon(selectedAgentType) }}</span>
                        <span class="message-role">{{ getAgentLabel(selectedAgentType) }}</span>
                    </div>
                    <div class="message-content">
                        <BSkeleton animation="wave" width="85%" />
                        <BSkeleton animation="wave" width="55%" />
                        <BSkeleton animation="wave" width="70%" />
                    </div>
                </div>
            </div>
        </div>

        <div class="card-footer">
            <div class="chat-input-container">
                <label for="chat-input" class="sr-only">Chat message</label>
                <textarea
                    id="chat-input"
                    v-model="query"
                    :disabled="busy"
                    placeholder="Ask me anything about Galaxy..."
                    rows="2"
                    class="form-control chat-input"
                    @keydown.enter.prevent="!$event.shiftKey && submitQuery()" />
                <button :disabled="busy || !query.trim()" class="btn btn-primary send-button" @click="submitQuery">
                    <FontAwesomeIcon v-if="!busy" :icon="faPaperPlane" fixed-width />
                    <LoadingSpan v-else message="" />
                </button>
            </div>
            <div class="chat-hints">
                <small class="text-muted">
                    Press Enter to send, Shift+Enter for new line. Try asking about tools, errors, or workflows!
                </small>
            </div>
        </div>
    </div>
</template>

<style lang="scss" scoped>
.chatgxy-container {
    height: 80vh;
    display: flex;
    flex-direction: column;

    .card-body {
        flex: 1;
        overflow: hidden;
        padding: 0;
    }

    .card-footer {
        flex-shrink: 0;
        position: sticky;
        bottom: 0;
        background: white;
        border-top: 1px solid #dee2e6;
        z-index: 10;
    }
}

.agent-selector {
    display: flex;
    align-items: center;

    select {
        width: 200px;
    }
}

.chat-messages {
    height: 100%;
    overflow-y: auto;
    padding: 1rem;
    background: #f8f9fa;
}

.analysis-steps {
    border: 1px solid #dee2e6;
    border-radius: 6px;
    padding: 0.75rem;
    background: white;
}

.analysis-step + .analysis-step {
    margin-top: 0.75rem;
}

.analysis-step-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-weight: 600;
    font-size: 0.9rem;
}

.analysis-step-header .step-label {
    text-transform: capitalize;
}

.analysis-step-header .step-status {
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    padding: 0.1rem 0.4rem;
    border-radius: 9999px;
    margin-left: 0.5rem;
}

.analysis-step.running .step-status {
    background: #fff3cd;
    color: #856404;
}

.analysis-step.completed .step-status {
    background: #d4edda;
    color: #155724;
}

.analysis-step.error .step-status {
    background: #f8d7da;
    color: #721c24;
}

.analysis-step-body {
    margin-top: 0.5rem;
    font-size: 0.9rem;
}

.analysis-step-body pre {
    background: #212529;
    color: #f8f9fa;
    padding: 0.5rem;
    border-radius: 4px;
    white-space: pre-wrap;
}

.analysis-step-body .text-danger {
    color: #dc3545 !important;
}

.pyodide-status {
    border: 1px dashed #6c757d;
    background: #f8f9fa;
}

.pyodide-status .pyodide-stream {
    background: #1e1e1e;
    color: #f8f9fa;
    padding: 0.5rem;
    border-radius: 4px;
    max-height: 200px;
    overflow: auto;
    font-family: var(--font-family-monospace);
    font-size: 0.85rem;
}

.pyodide-status .pyodide-stream.text-danger {
    color: #f8d7da;
}

.plot-preview {
    max-width: 320px;
    border: 1px solid #dee2e6;
    border-radius: 4px;
    background: #fff;
}

.step-requirements {
    margin-top: 0.35rem;
    font-size: 0.75rem;
}

.dataset-selector-panel {
    background: white;
    border: 1px solid #dee2e6;
}











.message {
    margin-bottom: 1.5rem;
    animation: fadeIn 0.3s;

    &.user-message {
        .message-content {
            background: #007bff;
            color: white;
            margin-left: 2rem;
            margin-right: 0;
            border-radius: 18px 18px 4px 18px;
        }
    }

    &.assistant-message {
        .message-content {
            background: white;
            color: #333;
            margin-left: 0;
            margin-right: 2rem;
            border-radius: 18px 18px 18px 4px;
            border: 1px solid #dee2e6;
        }
    }
}

.message-header {
    display: flex;
    align-items: center;
    margin-bottom: 0.5rem;
    font-size: 0.875rem;
    color: #6c757d;

    .message-icon {
        margin-right: 0.5rem;
    }

    .message-role {
        font-weight: 600;
        margin-right: 0.5rem;
    }
}

.message-agent-info {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.5rem;

    .agent-icon {
        font-size: 1.1rem;
    }

    .agent-name {
        font-weight: 600;
        color: #495057;
    }
}

.agent-description {
    font-size: 0.75rem;
    color: #6c757d;
    font-style: italic;
    margin-left: 1.6rem;
    margin-bottom: 0.25rem;
}

.message-badges {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-left: 1.6rem;
}

.confidence-badge {
    padding: 0.125rem 0.5rem;
    border-radius: 12px;
    font-size: 0.75rem;
    font-weight: 600;

    &.confidence-high {
        background: #d4edda;
        color: #155724;
    }

    &.confidence-medium {
        background: #fff3cd;
        color: #856404;
    }

    &.confidence-low {
        background: #f8d7da;
        color: #721c24;
    }
}

.routing-info {
    margin-left: 0.5rem;
    padding: 0.125rem 0.5rem;
    background: #e7f3ff;
    color: #0056b3;
    border-radius: 10px;
    font-size: 0.75rem;
    font-weight: 500;
    cursor: help;

    &:hover {
        background: #d1e7fd;
    }
}

.message-content {
    padding: 0.75rem 1rem;
    word-wrap: break-word;

    ::v-deep {
        p:last-child {
            margin-bottom: 0;
        }

        code {
            background: rgba(0, 0, 0, 0.05);
            padding: 0.125rem 0.25rem;
            border-radius: 3px;
        }

        pre {
            background: #f6f8fa;
            padding: 0.75rem;
            border-radius: 6px;
            overflow-x: auto;
        }
    }
}

.message-feedback {
    margin-top: 0.5rem;
    margin-left: 2rem;

    .feedback-given {
        color: #28a745;
    }

    .feedback-text {
        font-size: 0.75rem;
        color: #6c757d;
        margin-left: 0.5rem;
    }
}

.chat-input-container {
    display: flex;
    gap: 0.5rem;
    align-items: flex-end;
    min-height: 44px; /* Ensure consistent height */

    .chat-input {
        flex: 1;
        resize: none; /* Prevent manual resizing */
        transition: none; /* Prevent transition effects */
    }

    .send-button {
        flex-shrink: 0;
        align-self: flex-end;
    }
}

.chat-hints {
    margin-top: 0.5rem;
    text-align: center;
}

@keyframes fadeIn {
    from {
        opacity: 0;
        transform: translateY(10px);
    }
    to {
        opacity: 1;
        transform: translateY(0);
    }
}

.sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
}

// History Sidebar Styles
.history-sidebar {
    width: 300px;
    border-right: 1px solid #dee2e6;
    background: white;
    display: flex;
    flex-direction: column;

    .history-header {
        padding: 1rem;
        border-bottom: 1px solid #dee2e6;
        display: flex;
        justify-content: space-between;
        align-items: center;

        h5 {
            margin: 0;
            font-size: 1rem;
        }
    }

    .history-list {
        flex: 1;
        overflow-y: auto;
    }

    .history-item {
        padding: 0.75rem 1rem;
        border-bottom: 1px solid #f0f0f0;
        cursor: pointer;
        transition: background-color 0.2s;

        &:hover {
            background-color: #f8f9fa;
        }

        .history-query {
            font-size: 0.875rem;
            color: #333;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            margin-bottom: 0.25rem;
        }

        .history-meta {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.75rem;
            color: #6c757d;

            .history-time {
                display: flex;
                align-items: center;
            }
        }
    }
}

</style>
