/**
 * Requests history items by reacting to changes of filter props passed
 * to the history items provider used in the history panel.
 */

import { reverse } from "lodash";
import { LastQueue } from "utils/promise-queue";
import { urlData } from "utils/url";
import { getFilterDict, mergeArray } from "./utilities";

const limit = 100;
const queue = new LastQueue();

// fields that can be used for text searches
const validFields = new Set(["name", "history_content_type", "type", "format", "extension", "state", "hid", "tags"]);

const state = {
    items: {},
    itemKey: "hid",
};

const getters = {
    getHistoryItems:
        (state) =>
        ({ historyId, filterText, showDeleted, showHidden }) => {
            const itemArray = state.items[historyId] || [];
            const filtered = itemArray.filter((item) => {
                if (!item) {
                    return false;
                }
                if (showDeleted != item.deleted) {
                    return false;
                }
                if (showHidden == item.visible) {
                    return false;
                }
                const filterDict = getFilterDict(filterText, validFields);
                for (const [key, value] of Object.entries(filterDict)) {
                    const itemValue = String(item[key]).toLowerCase();
                    const filterValue = String(value).toLowerCase();
                    if (!itemValue.includes(filterValue)) {
                        return false;
                    }
                }
                return true;
            });
            return reverse(filtered);
        },
};

const getQueryString = (filterText, showDeleted, showHidden) => {
    const deleted = showDeleted ? "True" : "False";
    const visible = showHidden ? "False" : "True";
    const filterDict = {
        ...getFilterDict(filterText, validFields),
        deleted: deleted,
        visible: visible,
    };
    const queryString = Object.entries(filterDict)
        .map(([f, v]) => `q=${f}&qv=${v}`)
        .join("&");
    return queryString;
};

const actions = {
    fetchHistoryItems: async ({ commit, dispatch }, { historyId, offset, filterText, showDeleted, showHidden }) => {
        dispatch("startHistoryChangedItems", { historyId: historyId });
        const queryString = getQueryString(filterText, showDeleted, showHidden);
        const params = `v=dev&order=hid&offset=${offset}&limit=${limit}`;
        const url = `api/histories/${historyId}/contents?${params}&${queryString}`;
        await queue.enqueue(urlData, { url }).then((payload) => {
            commit("saveHistoryItems", { historyId, payload });
        });
    },
};

const mutations = {
    saveHistoryItems: (state, { historyId, payload }) => {
        mergeArray(historyId, payload, state.items, state.itemKey);
    },
};

export const historyItemsStore = {
    state,
    getters,
    actions,
    mutations,
};
