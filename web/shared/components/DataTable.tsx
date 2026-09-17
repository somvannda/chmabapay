"use client";

import React from "react";
import { brandColors, radius, shadows, fonts } from "../theme";

export interface ColumnDef<T> {
  key: keyof T | string;
  header: string;
  accessor?: (row: T) => React.ReactNode;
  width?: string;
  align?: "left" | "center" | "right";
}

interface DataTableProps<T> {
  columns: ColumnDef<T>[];
  data: T[];
  emptyState?: React.ReactNode;
  onRowClick?: (row: T) => void;
  className?: string;
}

export function DataTable<T extends object>({
  columns,
  data,
  emptyState,
  onRowClick,
  className,
}: DataTableProps<T>) {
  return (
    <div
      className={className}
      style={{
        backgroundColor: brandColors.surfaceCard,
        borderRadius: `${radius.card}px`,
        boxShadow: shadows.card,
        overflow: "hidden",
        border: `1px solid rgba(15, 23, 42, 0.06)`,
      }}
    >
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: fonts.sans }}>
          <thead>
            <tr style={{
              backgroundColor: "#FAFBFC",
              borderBottom: `1px solid rgba(15, 23, 42, 0.06)`,
            }}>
              {columns.map((col) => (
                <th
                  key={String(col.key)}
                  style={{
                    padding: "14px 16px",
                    textAlign: col.align ?? "left",
                    fontSize: "12px",
                    fontWeight: 600,
                    color: brandColors.subtleText,
                    letterSpacing: "0.04em",
                    textTransform: "uppercase",
                    whiteSpace: "nowrap",
                    width: col.width,
                  }}
                >
                  {col.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.length === 0 ? (
              <tr>
                <td
                  colSpan={columns.length}
                  style={{
                    padding: "48px 16px",
                    textAlign: "center",
                    color: brandColors.textBody,
                    fontSize: "14px",
                  }}
                >
                  {emptyState ?? "No data"}
                </td>
              </tr>
            ) : (
              data.map((row, i) => (
                <tr
                  key={i}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  style={{
                    borderBottom: i < data.length - 1 ? `1px solid rgba(15, 23, 42, 0.05)` : "none",
                    cursor: onRowClick ? "pointer" : "default",
                    transition: "background-color 0.12s ease",
                  }}
                  onMouseEnter={(e) => {
                    if (onRowClick) (e.currentTarget as HTMLTableRowElement).style.backgroundColor = "#FAFBFC";
                  }}
                  onMouseLeave={(e) => {
                    if (onRowClick) (e.currentTarget as HTMLTableRowElement).style.backgroundColor = "transparent";
                  }}
                >
                  {columns.map((col) => {
                    const value = col.accessor
                      ? col.accessor(row)
                      : (row[col.key as keyof T] as React.ReactNode);
                    return (
                      <td
                        key={String(col.key)}
                        style={{
                          padding: "14px 16px",
                          textAlign: col.align ?? "left",
                          fontSize: "14px",
                          color: brandColors.brandInk,
                          verticalAlign: "middle",
                        }}
                      >
                        {value}
                      </td>
                    );
                  })}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default DataTable;
