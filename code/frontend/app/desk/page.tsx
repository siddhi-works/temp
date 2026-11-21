/** Staff desk page (/desk): mounts the desk app. */

import Desk from "./Desk";

export const metadata = { title: "College desk", robots: { index: false } };

export default function Page() {
  return <Desk />;
}
