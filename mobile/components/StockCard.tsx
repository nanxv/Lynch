import { StatusBadge } from "@/components/StatusBadge";
import type { StatusItem } from "@/lib/types";
import { Pressable, StyleSheet, Text, View } from "react-native";

function formatPrice(value: number | null, market: string): string {
  if (value == null) return "—";
  if (market === "JP") return `¥${value.toLocaleString("ja-JP", { maximumFractionDigits: 0 })}`;
  return `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatPct(value: number | null): string {
  if (value == null) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

export function StockCard({
  item,
  onPress,
}: {
  item: StatusItem;
  onPress?: () => void;
}) {
  const borderColor =
    item.ui_status === "signal"
      ? "#22C55E"
      : item.ui_status === "danger"
        ? "#EF4444"
        : item.ui_status === "ambush"
          ? "#EAB308"
          : "#4B5563";

  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [
        styles.card,
        { borderColor },
        pressed && styles.pressed,
      ]}
    >
      <View style={styles.header}>
        <View style={styles.titleBlock}>
          <Text style={styles.name}>{item.name}</Text>
          <Text style={styles.ticker}>
            {item.ticker} · Tier {item.tier} · {item.market}
          </Text>
        </View>
        <StatusBadge status={item.ui_status} />
      </View>

      <View style={styles.metrics}>
        <Metric label="现价" value={formatPrice(item.price, item.market)} />
        <Metric label="周线 D" value={formatPct(item.deviation_pct)} highlight />
        <Metric label="日线偏离" value={formatPct(item.daily_gap_pct)} />
      </View>

      {item.market_index ? (
        <Text style={styles.marketLine}>
          大盘 {item.market_index}：{item.market_is_downtrend ? "跌势" : "非跌势"}
        </Text>
      ) : null}

      <Text style={styles.message} numberOfLines={3}>
        {item.error ?? item.message}
      </Text>
    </Pressable>
  );
}

function Metric({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight?: boolean;
}) {
  return (
    <View style={styles.metric}>
      <Text style={styles.metricLabel}>{label}</Text>
      <Text style={[styles.metricValue, highlight && styles.metricHighlight]}>
        {value}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: "#111827",
    borderRadius: 16,
    borderWidth: 1.5,
    padding: 16,
    gap: 12,
  },
  pressed: {
    opacity: 0.92,
  },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    gap: 12,
    alignItems: "flex-start",
  },
  titleBlock: {
    flex: 1,
    gap: 4,
  },
  name: {
    color: "#F9FAFB",
    fontSize: 18,
    fontWeight: "700",
  },
  ticker: {
    color: "#9CA3AF",
    fontSize: 13,
  },
  metrics: {
    flexDirection: "row",
    justifyContent: "space-between",
    gap: 8,
  },
  metric: {
    flex: 1,
    gap: 4,
  },
  metricLabel: {
    color: "#6B7280",
    fontSize: 11,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  metricValue: {
    color: "#E5E7EB",
    fontSize: 16,
    fontWeight: "600",
  },
  metricHighlight: {
    color: "#FDE68A",
  },
  marketLine: {
    color: "#9CA3AF",
    fontSize: 12,
  },
  message: {
    color: "#D1D5DB",
    fontSize: 13,
    lineHeight: 18,
  },
});
