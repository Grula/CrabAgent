import { describe, expect, it } from "vitest";
import { parseChartSpec, parseKpiSpec } from "./chartSchema";

describe("parseChartSpec", () => {
  it("accepts the current object series format", () => {
    const chart = parseChartSpec(JSON.stringify({
      version: 1, type: "bar", x: { field: "month" },
      series: [{ field: "revenue", name: "revenue" }],
      data: [{ month: "Jan", revenue: 120 }],
    }));
    expect(chart.x?.field).toBe("month");
    expect(chart.series[0]).toEqual({ field: "revenue", name: "revenue", color: undefined });
  });

  it("renders historical xField and string-series messages", () => {
    const chart = parseChartSpec(JSON.stringify({
      version: 1, type: "bar", xField: "month", series: ["revenue"],
      data: [{ month: "Jan", revenue: 120 }],
    }));
    expect(chart.x?.field).toBe("month");
    expect(chart.series[0].field).toBe("revenue");
  });

  it("infers a text category field when x is omitted", () => {
    const chart = parseChartSpec(JSON.stringify({
      version: 1, type: "line", series: ["revenue"],
      data: [{ month: "Jan", revenue: 120 }],
    }));
    expect(chart.x?.field).toBe("month");
  });

  it("rejects executable or oversized data", () => {
    expect(() => parseChartSpec(JSON.stringify({
      version: 1, type: "bar", series: ["revenue"],
      data: [{ month: "Jan", revenue: { value: 120 } }],
    }))).toThrow("invalid field");
  });
});

describe("parseKpiSpec", () => {
  it("accepts a valid KPI", () => {
    expect(parseKpiSpec(JSON.stringify({ version: 1, title: "conversion", value: "18.6%", trend: "up" }))).toMatchObject({ title: "conversion", trend: "up" });
  });
});
