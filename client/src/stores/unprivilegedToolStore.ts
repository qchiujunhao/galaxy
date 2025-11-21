import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { GalaxyApi, type UnprivilegedToolResponse } from "@/api";

export const useUnprivilegedToolStore = defineStore("unprivilegedToolStore", () => {
    const unprivilegedTools = ref<UnprivilegedToolResponse[]>();
    const canUseUnprivilegedTools = ref(false);
    const isLoading = ref(false);
    const loadError = ref<string | null>(null);
    const requiresAuthentication = ref(false);
    const isLoaded = computed(() => unprivilegedTools.value !== undefined);

    async function load(reload = false) {
        if (reload || (!isLoaded.value && !isLoading.value)) {
            isLoading.value = true;
            loadError.value = null;
            requiresAuthentication.value = false;
            try {
                const { data, error, response } = await GalaxyApi().GET("/api/unprivileged_tools");

                if (error) {
                    const status = response?.status ?? (error as any)?.status ?? (error as any)?.status_code;
                    if (status === 401 || status === 403) {
                        requiresAuthentication.value = true;
                        loadError.value = "Authentication required to manage custom tools.";
                    } else {
                        loadError.value = (error as any)?.error ?? "Failed to load custom tools.";
                    }
                    canUseUnprivilegedTools.value = false;
                    unprivilegedTools.value = undefined;
                } else {
                    unprivilegedTools.value = Array.isArray(data) ? data : [];
                    canUseUnprivilegedTools.value = true;
                }
            } catch (err) {
                console.error("Error loading unprivileged tools", err);
                loadError.value = "Failed to load custom tools.";
                canUseUnprivilegedTools.value = false;
                unprivilegedTools.value = undefined;
            } finally {
                isLoading.value = false;
            }
        }
        return unprivilegedTools;
    }

    async function deactivateTool(uuid: string) {
        if (unprivilegedTools.value) {
            isLoading.value = true;
            const { error } = await GalaxyApi().DELETE("/api/unprivileged_tools/{uuid}", {
                params: { path: { uuid } },
            });

            if (!error) {
                unprivilegedTools.value = unprivilegedTools.value.filter((tool) => tool.uuid !== uuid);
            }
            isLoading.value = false;
        }
    }

    load();

    return {
        canUseUnprivilegedTools,
        unprivilegedTools,
        isLoaded,
        isLoading,
        loadError,
        requiresAuthentication,
        load,
        deactivateTool,
    };
});
