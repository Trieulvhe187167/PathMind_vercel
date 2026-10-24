import { redirect } from "next/navigation";

export default function AdminConfigIndex() {
  redirect("/admin/config/connections");
}
