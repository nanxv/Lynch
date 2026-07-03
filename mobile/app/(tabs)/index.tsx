import { StockCard } from "@/components/StockCard";
import { API_URL } from "@/constants/config";
import { api } from "@/lib/api";
import type { StatusItem } from "@/lib/types";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

export default function RadarScreen() {
  const [items, setItems] = useState<StatusItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);

  const load = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    setError(null);
    try {
      const data = await api.getStatus("ALL");
      setItems(data);
      setLastUpdated(new Date().toLocaleString("ja-JP"));
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const signals = items.filter((i) => i.ui_status === "signal").length;
  const ambush = items.filter((i) => i.ui_status === "ambush").length;
  const danger = items.filter((i) => i.ui_status === "danger").length;

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
      <View style={styles.hero}>
        <Text style={styles.heroTitle}>铁律 2.5 · 狙击雷达</Text>
        <Text style={styles.heroSub}>API: {API_URL}</Text>
        {lastUpdated ? <Text style={styles.heroSub}>更新: {lastUpdated}</Text> : null}
      </View>

      <View style={styles.summaryRow}>
        <SummaryChip label="买点" value={signals} color="#22C55E" />
        <SummaryChip label="伏击" value={ambush} color="#EAB308" />
        <SummaryChip label="危险" value={danger} color="#EF4444" />
      </View>

      {loading && !refreshing ? (
        <ActivityIndicator color="#22C55E" style={{ marginTop: 40 }} />
      ) : null}

      {error ? (
        <View style={styles.errorBox}>
          <Text style={styles.errorTitle}>无法连接后端</Text>
          <Text style={styles.errorText}>{error}</Text>
          <Text style={styles.errorHint}>
            请确认 FastAPI 已启动，且模拟器可访问 {API_URL}
          </Text>
          <Pressable style={styles.retryBtn} onPress={() => load()}>
            <Text style={styles.retryText}>重试</Text>
          </Pressable>
        </View>
      ) : null}

      {!loading && !error
        ? items.map((item) => <StockCard key={item.ticker} item={item} />)
        : null}
    </ScrollView>
  );
}

function SummaryChip({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}) {
  return (
    <View style={[styles.summaryChip, { borderColor: color }]}>
      <Text style={[styles.summaryValue, { color }]}>{value}</Text>
      <Text style={styles.summaryLabel}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#0B0F14",
  },
  content: {
    padding: 16,
    gap: 14,
    paddingBottom: 32,
  },
  hero: {
    gap: 4,
    marginBottom: 4,
  },
  heroTitle: {
    color: "#F9FAFB",
    fontSize: 24,
    fontWeight: "800",
  },
  heroSub: {
    color: "#6B7280",
    fontSize: 12,
  },
  summaryRow: {
    flexDirection: "row",
    gap: 10,
  },
  summaryChip: {
    flex: 1,
    backgroundColor: "#111827",
    borderRadius: 12,
    borderWidth: 1,
    paddingVertical: 12,
    alignItems: "center",
    gap: 2,
  },
  summaryValue: {
    fontSize: 22,
    fontWeight: "800",
  },
  summaryLabel: {
    color: "#9CA3AF",
    fontSize: 12,
  },
  errorBox: {
    backgroundColor: "#1F2937",
    borderRadius: 14,
    padding: 16,
    gap: 8,
  },
  errorTitle: {
    color: "#FCA5A5",
    fontWeight: "700",
    fontSize: 16,
  },
  errorText: {
    color: "#D1D5DB",
    fontSize: 13,
  },
  errorHint: {
    color: "#9CA3AF",
    fontSize: 12,
    lineHeight: 18,
  },
  retryBtn: {
    alignSelf: "flex-start",
    backgroundColor: "#22C55E",
    borderRadius: 8,
    paddingHorizontal: 14,
    paddingVertical: 8,
    marginTop: 4,
  },
  retryText: {
    color: "#052E16",
    fontWeight: "700",
  },
});
