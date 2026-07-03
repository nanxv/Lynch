import { AddStockModal } from "@/components/AddStockModal";
import { api } from "@/lib/api";
import type { WatchlistItem } from "@/lib/types";
import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

export default function WatchlistScreen() {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);

  const load = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    try {
      setItems(await api.getWatchlist());
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleDelete(ticker: string, name: string) {
    Alert.alert("移除股票", `确定从监控池移除 ${name} (${ticker})？`, [
      { text: "取消", style: "cancel" },
      {
        text: "移除",
        style: "destructive",
        onPress: async () => {
          await api.removeWatchlist(ticker);
          await load(true);
        },
      },
    ]);
  }

  return (
    <View style={styles.container}>
      <View style={styles.toolbar}>
        <Text style={styles.count}>{items.length} 只监控中</Text>
        <Pressable style={styles.addBtn} onPress={() => setModalOpen(true)}>
          <Ionicons name="add" size={22} color="#052E16" />
          <Text style={styles.addText}>添加</Text>
        </Pressable>
      </View>

      {loading && !refreshing ? (
        <ActivityIndicator color="#22C55E" style={{ marginTop: 40 }} />
      ) : (
        <ScrollView
          contentContainerStyle={styles.list}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => load(true)}
              tintColor="#22C55E"
            />
          }
        >
          {items.map((item) => (
            <View key={item.ticker} style={styles.row}>
              <View style={styles.rowMain}>
                <Text style={styles.name}>{item.name}</Text>
                <Text style={styles.meta}>
                  {item.ticker} · {item.market} · Tier {item.tier}
                </Text>
                {item.note ? <Text style={styles.note}>{item.note}</Text> : null}
              </View>
              <Pressable
                onPress={() => handleDelete(item.ticker, item.name)}
                hitSlop={12}
              >
                <Ionicons name="trash-outline" size={20} color="#F87171" />
              </Pressable>
            </View>
          ))}
        </ScrollView>
      )}

      <AddStockModal
        visible={modalOpen}
        onClose={() => setModalOpen(false)}
        onSubmit={async (payload) => {
          await api.addWatchlist(payload);
          await load(true);
        }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#0B0F14",
  },
  toolbar: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: "#1F2937",
  },
  count: {
    color: "#9CA3AF",
    fontSize: 14,
  },
  addBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    backgroundColor: "#22C55E",
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 8,
  },
  addText: {
    color: "#052E16",
    fontWeight: "700",
  },
  list: {
    padding: 16,
    gap: 10,
  },
  row: {
    backgroundColor: "#111827",
    borderRadius: 14,
    padding: 14,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    borderWidth: 1,
    borderColor: "#1F2937",
  },
  rowMain: {
    flex: 1,
    gap: 4,
  },
  name: {
    color: "#F9FAFB",
    fontSize: 17,
    fontWeight: "700",
  },
  meta: {
    color: "#9CA3AF",
    fontSize: 13,
  },
  note: {
    color: "#6B7280",
    fontSize: 12,
  },
});
