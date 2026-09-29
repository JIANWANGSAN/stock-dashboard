export interface GroundedFinding { text: string; citations: string[]; }
export interface ReportEvidence {
  id: string;
  label: string;
  kind: "input_excerpt" | "metric" | "comparison";
  target_date: string;
  text: string;
  context?: string;
  note: string;
  inputs?: string[];
}
export interface GroundedReport {
  version: 1;
  status: "references_validated";
  note: string;
  records: ReportEvidence[];
  sections: { key: string; title: string; findings: GroundedFinding[] }[];
  summary: {
    market_oneliner: GroundedFinding;
    focus_directions: { direction: string; logic: GroundedFinding; risk: GroundedFinding }[];
    risk_alerts: GroundedFinding[];
    verification_items: { metric: string; direction: string; reason: GroundedFinding }[];
  };
}
