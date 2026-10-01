import type { IconName } from "@/components/icons";

export type NavItem = {
  href: string;
  label: string;
  icon: IconName;
  description: string;
};

export const navItems: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: "dashboard", description: "Overview of your workspace" },
  { href: "/studio", label: "Magic Studio", icon: "studio", description: "Turn a rough idea into a polished prompt" },
  { href: "/library", label: "Library", icon: "library", description: "Saved prompts and starting recipes" },
  { href: "/projects", label: "Projects", icon: "projects", description: "Organize prompts by body of work" },
  { href: "/lab", label: "Lab", icon: "lab", description: "Quick tools and future experiments" },
  { href: "/settings", label: "Settings", icon: "settings", description: "Appearance, session and system status" },
];

export function navItemForPath(pathname: string): NavItem | undefined {
  return navItems.find((item) => item.href === pathname);
}