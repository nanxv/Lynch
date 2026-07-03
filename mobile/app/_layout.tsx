import { registerForPushNotifications } from "@/lib/notifications";
import { Stack } from "expo-router";
import { useEffect } from "react";
import { StatusBar } from "expo-status-bar";

export default function RootLayout() {
  useEffect(() => {
    registerForPushNotifications().catch(console.warn);
  }, []);

  return (
    <>
      <StatusBar style="light" />
      <Stack screenOptions={{ headerShown: false }}>
        <Stack.Screen name="(tabs)" />
      </Stack>
    </>
  );
}
