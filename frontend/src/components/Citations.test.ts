import { describe, expect, it } from "vitest";
import { resolveCitationHref } from "./Citations";

// M9 T6 (docs/specs/m9_google_drive_ingestion.md §4.7): Citations.tsx の href 分岐を
// 純粋関数として切り出し、コンポーネントをレンダリングせずに検証する。この repo の
// frontend には @testing-library/react が入っておらず、src/lib/*.test.ts と同じ
// 「プレーン関数を直接テストする」流儀に合わせている（Citations.test.tsx は作らない）。

describe("resolveCitationHref", () => {
  it("uses file:// with the local path when source_type is local_fs", () => {
    const href = resolveCitationHref({
      n: 1,
      title: "Local Doc",
      heading: "h1",
      path: "docs/local.md",
      source_type: "local_fs",
    });
    expect(href).toBe("file://docs/local.md");
  });

  it("falls back to file:// when source_type is absent (pre-M9 citation payload shape)", () => {
    const href = resolveCitationHref({
      n: 1,
      title: "Local Doc",
      heading: "h1",
      path: "docs/local.md",
    });
    expect(href).toBe("file://docs/local.md");
  });

  it("falls back to # when there is no path and no source_type", () => {
    const href = resolveCitationHref({
      n: 1,
      title: "No Path Doc",
      heading: "h1",
    });
    expect(href).toBe("#");
  });

  it("uses source_url directly when source_type is google_drive", () => {
    const href = resolveCitationHref({
      n: 1,
      title: "Drive Doc",
      heading: "h1",
      path: "Notes/drive-doc.md",
      source_type: "google_drive",
      source_url: "https://drive.google.com/file/d/drv-abc123/view",
    });
    expect(href).toBe("https://drive.google.com/file/d/drv-abc123/view");
  });

  it("falls back to file:// when source_type is google_drive but source_url is missing", () => {
    const href = resolveCitationHref({
      n: 1,
      title: "Drive Doc",
      heading: "h1",
      path: "Notes/drive-doc.md",
      source_type: "google_drive",
    });
    expect(href).toBe("file://Notes/drive-doc.md");
  });
});
