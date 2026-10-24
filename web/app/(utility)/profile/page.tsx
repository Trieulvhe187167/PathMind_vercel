import { redirect } from "next/navigation";

/** The profile now lives in Settings. */
export default function ProfilePage() {
  redirect("/settings/profile");
}
