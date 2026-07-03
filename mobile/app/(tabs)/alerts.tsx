import { api } from "@/lib/api";
import type { AlertHistoryItem } from "@/lib/types";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString("ja-JP");
  } catch {
    return iso;
  }
}

export default function AlertsScreen() {
  const [items, setItems] = useState<AlertHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    try {
      setItems(await api.getAlerts());
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={() => load(true)}
          tintColor="#22C55E"
        />
      }
    >
      {loading && !refreshing ? (
        <ActivityIndicator color="#22C55E" style={{ marginTop: 40 }} />
      ) : null}

      {!loading && items.length === 0 ? (
        <View style={styles.empty}>
          <Text style={styles.emptyTitle}>暂无历史信号</Text>
          <Text style={styles.emptyText}>
            当系统触发核心阻击或常规买入时，记录会出现在这里。
          </Text>
        </View>
      ) : null}

      {items.map((item, index) => (
        <View key={item.id} style={styles.timelineItem}>
          <View style={styles.lineCol}>
            <View style={styles.dot} />
            {index < items.length - 1 ? <View style={styles.line} /> : null}
          </View>
          <View style={styles.card}>
            <Text style={styles.time}>{formatTime(item.created_at)}</Text>
            <Text style={styles.title}>{item.title}</Text>
            <Text style={styles.meta}>
              {item.name} ({item.ticker}) · Tier {item.tier} · {item.market}
            </Text>
            <Text style={styles.body}>{item.body}</Text>
          </View>
        </View>
      ))}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#0B0F14",
  },
  content: {
    padding: 16,
    paddingBottom: 32,
  },
  empty: {
    backgroundColor: "#111827",
    borderRadius: 14,
    padding: 20,
    gap: 8,
    marginTop: 20,
  },
  emptyTitle: {
    color: "#F9FAFB",
    fontSize: 16,
    fontWeight: "700",
  },
  emptyText: {
    color: "#9CA3AF",
    fontSize: 13,
    lineHeight: 18,
  },
  timelineItem: {
    flexDirection: "row",
    gap: 12,
  },
  lineCol: {
    width: 16,
    alignItems: "center",
  },
  dot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: "#22C55E",
    marginTop: 18,
  },
  line: {
    flex: 1,
    width: 2,
    backgroundColor: "#1F2937",
    marginTop: 4,
  },
  card: {
    flex: 1,
    backgroundColor: "#111827",
    borderRadius: 14,
    padding: 14,
    marginBottom: 12,
    gap: 6,
    borderWidth: 1,
    borderColor: "#1F2937",
  },
  time: {
    color: "#6B7280",
    fontSize: 11,
  },
  title: {
    color: "#86EFAC",
    fontSize: 16,
    fontWeight: "700",
  },
  meta: {
    color: "#9CA3AF",
    fontSize: 12,
  },
  body: {
    color: "#D1D5DB",
    fontSize: 13,
    lineHeight: 18,
  },
});
