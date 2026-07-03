import type { WatchlistCreate } from "@/lib/types";
import { useState } from "react";
import {
  Modal,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

export function AddStockModal({
  visible,
  onClose,
  onSubmit,
}: {
  visible: boolean;
  onClose: () => void;
  onSubmit: (payload: WatchlistCreate) => Promise<void>;
}) {
  const [ticker, setTicker] = useState("");
  const [name, setName] = useState("");
  const [market, setMarket] = useState<"JP" | "US">("JP");
  const [tier, setTier] = useState<1 | 2>(2);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await onSubmit({
        ticker: ticker.trim().toUpperCase(),
        name: name.trim(),
        market,
        tier,
        note: note.trim(),
      });
      setTicker("");
      setName("");
      setNote("");
      setMarket("JP");
      setTier(2);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={styles.overlay}>
        <View style={styles.sheet}>
          <Text style={styles.title}>添加监控股票</Text>

          <Field label="代码" value={ticker} onChangeText={setTicker} placeholder="4063.T / AMD" />
          <Field label="名称" value={name} onChangeText={setName} placeholder="信越化学" />
          <Field label="备注" value={note} onChangeText={setNote} placeholder="可选" />

          <Text style={styles.label}>市场</Text>
          <View style={styles.row}>
            <Chip label="日股 JP" active={market === "JP"} onPress={() => setMarket("JP")} />
            <Chip label="美股 US" active={market === "US"} onPress={() => setMarket("US")} />
          </View>

          <Text style={styles.label}>Tier</Text>
          <View style={styles.row}>
            <Chip label="Tier 1 核心池" active={tier === 1} onPress={() => setTier(1)} />
            <Chip label="Tier 2 常规池" active={tier === 2} onPress={() => setTier(2)} />
          </View>

          {error ? <Text style={styles.error}>{error}</Text> : null}

          <View style={styles.actions}>
            <Pressable style={styles.secondaryBtn} onPress={onClose}>
              <Text style={styles.secondaryText}>取消</Text>
            </Pressable>
            <Pressable
              style={[styles.primaryBtn, saving && styles.disabled]}
              onPress={handleSave}
              disabled={saving || !ticker.trim() || !name.trim()}
            >
              <Text style={styles.primaryText}>{saving ? "保存中…" : "添加"}</Text>
            </Pressable>
          </View>
        </View>
      </View>
    </Modal>
  );
}

function Field({
  label,
  value,
  onChangeText,
  placeholder,
}: {
  label: string;
  value: string;
  onChangeText: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        style={styles.input}
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor="#6B7280"
        autoCapitalize="characters"
      />
    </View>
  );
}

function Chip({
  label,
  active,
  onPress,
}: {
  label: string;
  active: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable
      onPress={onPress}
      style={[styles.chip, active && styles.chipActive]}
    >
      <Text style={[styles.chipText, active && styles.chipTextActive]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.6)",
    justifyContent: "flex-end",
  },
  sheet: {
    backgroundColor: "#111827",
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    padding: 20,
    gap: 12,
  },
  title: {
    color: "#F9FAFB",
    fontSize: 20,
    fontWeight: "700",
    marginBottom: 4,
  },
  field: {
    gap: 6,
  },
  label: {
    color: "#9CA3AF",
    fontSize: 12,
    fontWeight: "600",
  },
  input: {
    backgroundColor: "#1F2937",
    color: "#F9FAFB",
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 16,
  },
  row: {
    flexDirection: "row",
    gap: 8,
    flexWrap: "wrap",
  },
  chip: {
    borderRadius: 999,
    borderWidth: 1,
    borderColor: "#374151",
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  chipActive: {
    backgroundColor: "#14532D",
    borderColor: "#22C55E",
  },
  chipText: {
    color: "#D1D5DB",
    fontSize: 13,
    fontWeight: "600",
  },
  chipTextActive: {
    color: "#86EFAC",
  },
  error: {
    color: "#FCA5A5",
    fontSize: 13,
  },
  actions: {
    flexDirection: "row",
    gap: 10,
    marginTop: 8,
  },
  secondaryBtn: {
    flex: 1,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#374151",
    paddingVertical: 14,
    alignItems: "center",
  },
  secondaryText: {
    color: "#E5E7EB",
    fontWeight: "600",
  },
  primaryBtn: {
    flex: 1,
    borderRadius: 12,
    backgroundColor: "#22C55E",
    paddingVertical: 14,
    alignItems: "center",
  },
  disabled: {
    opacity: 0.6,
  },
  primaryText: {
    color: "#052E16",
    fontWeight: "700",
  },
});
