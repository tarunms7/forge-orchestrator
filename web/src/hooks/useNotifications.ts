"use client";

export function useNotifications() {
  const notify = (title: string, body: string) => {
    if (
      typeof window !== "undefined" &&
      "Notification" in window &&
      Notification.permission === "granted"
    ) {
      new Notification(title, { body });
    }
  };

  const requestPermission = async () => {
    if (typeof window !== "undefined" && "Notification" in window) {
      return Notification.requestPermission();
    }
  };

  return { notify, requestPermission };
}
