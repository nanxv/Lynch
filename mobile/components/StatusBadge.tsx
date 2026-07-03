import type { UiStatus } from "@/lib/types";
import { StyleSheet, Text, View } from "react-native";

const META: Record<
  UiStatus,
  { label: string; emoji: string; bg: string; fg: string }
> = {
  danger: { label: "危险区", emoji: "🔴", bg: "#3F1D1D", fg: "#FCA5A5" },
  ambush: { label: "伏击圈", emoji: "🟡", bg: "#3F3619", fg: "#FDE68A" },
  signal: { label: "绝佳买点", emoji: "🟢", bg: "#143524", fg: "#86EFAC" },
  error: { label: "数据异常", emoji: "⚪", bg: "#1F2937", fg: "#D1D5DB" },
};

export function StatusBadge({ status }: { status: UiStatus }) {
  const meta = META[status] ?? META.error;
  return (
    <View style={[styles.badge, { backgroundColor: meta.bg }]}>
      <Text style={[styles.text, { color: meta.fg }]}>
        {meta.emoji} {meta.label}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: {
    alignSelf: "flex-start",
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  text: {
    fontSize: 12,
    fontWeight: "700",
  },
});
