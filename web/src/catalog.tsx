/**
 * Yutrix lending catalog (A2UI v1.0).
 *
 * The agent can only compose these components. Every value is bound with
 * @path to data the BFF filled from the core; the agent never sends numbers.
 * ApprovalPanel buttons raise an `approve_sanction` action that the BFF
 * handles itself (one-time approval token), never the agent.
 */
import {createComponentImplementation} from '@a2ui/react';
import {Catalog} from '@a2ui/web_core';
import {CommonSchemas} from '@a2ui/web_core/v1_0';
import {z} from 'zod';

export const LENDING_CATALOG_ID = 'https://yutrix.io/a2ui/catalogs/lending/v1.json';

const inr = (n: unknown) =>
  typeof n === 'number' ? '₹' + n.toLocaleString('en-IN', {maximumFractionDigits: 0}) : '—';
type Rec = Record<string, any>;
const obj = (v: unknown): Rec => (v && typeof v === 'object' ? (v as Rec) : {});
const arr = (v: unknown): Rec[] => (Array.isArray(v) ? (v as Rec[]) : []);

function Denied({value}: {value: Rec}) {
  return value._denied ? <div className="deny">Not available: {String(value._denied)}</div> : null;
}

// ------------------------------------------------------------------ layout
const Stack = createComponentImplementation(
  {name: 'Stack', schema: z.object({children: CommonSchemas.ChildList})},
  ({props, buildChild}) => {
    const kids = (props.children ?? []) as any[];
    return (
      <div className="stack">
        {kids.map((c, i) => (
          <div key={i}>{typeof c === 'string' ? buildChild(c) : buildChild(c.id ?? c.componentId, c.basePath)}</div>
        ))}
      </div>
    );
  },
);

const Heading = createComponentImplementation(
  {name: 'Heading', schema: z.object({text: CommonSchemas.DynamicString})},
  ({props}) => <h2 className="heading">{props.text}</h2>,
);

const AccessNotice = createComponentImplementation(
  {
    name: 'AccessNotice',
    schema: z.object({
      title: CommonSchemas.DynamicString,
      code: CommonSchemas.DynamicString,
      message: CommonSchemas.DynamicString,
    }),
  },
  ({props}) => (
    <div className="card notice">
      <div className="notice-title">⛔ {props.title}</div>
      <div>{props.message}</div>
      <code>{props.code}</code>
    </div>
  ),
);

// ------------------------------------------------------------------ lending
const RecommendationBanner = createComponentImplementation(
  {
    name: 'RecommendationBanner',
    schema: z.object({
      recommendation: CommonSchemas.DynamicString,
      ruleset: CommonSchemas.DynamicString,
      metrics: CommonSchemas.DynamicValue,
    }),
  },
  ({props}) => {
    const m = obj(props.metrics);
    return (
      <div className={`card banner rec-${(props.recommendation ?? '').toLowerCase()}`}>
        <div>
          <div className="label">Core recommendation</div>
          <div className="rec">{props.recommendation ?? '…'}</div>
          <div className="muted">Ruleset {props.ruleset}</div>
        </div>
        <div className="metrics">
          <Metric k="FOIR" v={m.foir_pct !== undefined ? `${m.foir_pct}%` : '—'} />
          <Metric k="Bureau" v={m.bureau_score ?? '—'} />
          <Metric k="Bounces 12m" v={m.bounces_12m ?? '—'} />
          <Metric k="Max DPD" v={m.max_dpd ?? '—'} />
          <Metric k="Proposed EMI" v={inr(m.proposed_emi)} />
        </div>
      </div>
    );
  },
);

function Metric({k, v}: {k: string; v: React.ReactNode}) {
  return (
    <div className="metric">
      <div className="label">{k}</div>
      <div className="value">{v}</div>
    </div>
  );
}

const CaseSummaryCard = createComponentImplementation(
  {name: 'CaseSummaryCard', schema: z.object({case: CommonSchemas.DynamicValue})},
  ({props}) => {
    const c = obj(props.case);
    if (c._denied) return <Denied value={c} />;
    return (
      <div className="card">
        <div className="card-title">
          {c.applicant} {c.business ? <span className="muted">· {c.business}</span> : null}
        </div>
        <div className="grid">
          <Metric k="Product" v={c.product} />
          <Metric k="Segment" v={c.segment} />
          <Metric k="Amount" v={inr(c.amount)} />
          <Metric k="Tenure" v={c.tenure_months ? `${c.tenure_months} months` : '—'} />
          <Metric k="Rate" v={c.rate ? `${c.rate}%` : '—'} />
          <Metric k="EMI" v={inr(c.emi)} />
          <Metric k="Income / month" v={inr(c.monthly_income)} />
          <Metric k="Branch" v={c.branch} />
          <Metric k="Stage" v={c.stage} />
          <Metric k="PAN" v={c.pan} />
        </div>
      </div>
    );
  },
);

const ReasonCodeList = createComponentImplementation(
  {name: 'ReasonCodeList', schema: z.object({items: CommonSchemas.DynamicValue})},
  ({props}) => (
    <div className="card">
      <div className="card-title">Rule results (explanation of record)</div>
      <table>
        <tbody>
          {arr(props.items).map(r => (
            <tr key={r.code}>
              <td>
                <code>{r.code}</code>
              </td>
              <td>{r.rule}</td>
              <td>
                <span className={`pill ${String(r.outcome).toLowerCase()}`}>{r.outcome}</span>
              </td>
              <td className="muted">{r.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ),
);

const BureauSummary = createComponentImplementation(
  {name: 'BureauSummary', schema: z.object({bureau: CommonSchemas.DynamicValue})},
  ({props}) => {
    const b = obj(props.bureau);
    if (b._denied) return <Denied value={b} />;
    return (
      <div className="card">
        <div className="card-title">Bureau summary</div>
        <div className="grid">
          <Metric k="Score" v={b.score} />
          <Metric k="Active loans" v={b.active_loans} />
          <Metric k="Enquiries 6m" v={b.enquiries_6m} />
          <Metric k="Max DPD 24m" v={b.max_dpd_24m} />
          <Metric k="PAN (masked)" v={b.pan} />
        </div>
      </div>
    );
  },
);

const DpdTrend = createComponentImplementation(
  {name: 'DpdTrend', schema: z.object({history: CommonSchemas.DynamicValue})},
  ({props}) => {
    const h = arr(props.history);
    const max = Math.max(30, ...h.map(x => x.dpd ?? 0));
    return (
      <div className="card">
        <div className="card-title">
          Repayment history · days past due (18 months)
          {h.length > 0 && h.every(x => !x.dpd) ? <span className="muted"> · no late payments</span> : null}
        </div>
        <div className="bars">
          {h.map(x => (
            <div key={x.month} className="bar-col" title={`${x.month}: DPD ${x.dpd}`}>
              <div className={`bar ${x.dpd ? 'late' : ''}`} style={{height: `${Math.max(4, (x.dpd / max) * 70)}px`}} />
              <div className="bar-label">{String(x.month).slice(5)}</div>
            </div>
          ))}
        </div>
      </div>
    );
  },
);

const DocumentChecklist = createComponentImplementation(
  {name: 'DocumentChecklist', schema: z.object({documents: CommonSchemas.DynamicValue})},
  ({props}) => (
    <div className="card">
      <div className="card-title">Documents</div>
      <ul className="docs">
        {arr(props.documents).map(d => (
          <li key={d.doc}>
            {d.status === 'Received' ? '✅' : '⏳'} {d.doc} <span className="muted">· {d.status}</span>
          </li>
        ))}
      </ul>
    </div>
  ),
);

const CashflowSummary = createComponentImplementation(
  {name: 'CashflowSummary', schema: z.object({summary: CommonSchemas.DynamicValue})},
  ({props}) => {
    const s = obj(props.summary);
    const months = arr(s.months);
    const max = Math.max(1, ...months.map(m => Math.max(m.inflow, m.outflow)));
    return (
      <div className="card">
        <div className="grid">
          <Metric k="Transactions analysed" v={s.transactions_analysed} />
          <Metric k="Avg inflow / month" v={inr(s.avg_monthly_inflow)} />
          <Metric k="Avg outflow / month" v={inr(s.avg_monthly_outflow)} />
          <Metric k="Cheque bounces" v={s.total_bounces} />
        </div>
        <div className="bars cash">
          {months.map(m => (
            <div key={m.month} className="bar-col" title={`${m.month}: in ${inr(m.inflow)}, out ${inr(m.outflow)}`}>
              <div className="pair">
                <div className="bar in" style={{height: `${(m.inflow / max) * 80}px`}} />
                <div className="bar out" style={{height: `${(m.outflow / max) * 80}px`}} />
              </div>
              <div className="bar-label">
                {String(m.month).slice(5)}
                {m.bounces ? ' ⚠' : ''}
              </div>
            </div>
          ))}
        </div>
        <div className="muted small">Source: {s.source}. Green = inflow, grey = outflow, ⚠ = bounce.</div>
      </div>
    );
  },
);

const CollectionsCard = createComponentImplementation(
  {name: 'CollectionsCard', schema: z.object({summary: CommonSchemas.DynamicValue})},
  ({props}) => {
    const s = obj(props.summary);
    return (
      <div className="card">
        <div className="grid">
          <Metric k="Bucket" v={s.bucket} />
          <Metric k="Current DPD" v={s.current_dpd} />
          <Metric k="Overdue" v={inr(s.overdue_amount)} />
        </div>
        <div className="card-title">Promises to pay</div>
        <ul className="docs">
          {arr(s.promises).map((p, i) => (
            <li key={i}>
              {p.promise_date} · {inr(p.amount)} <span className="muted">· logged by {p.logged_by}</span>
            </li>
          ))}
        </ul>
      </div>
    );
  },
);

const ApprovalPanel = createComponentImplementation(
  {
    name: 'ApprovalPanel',
    schema: z.object({
      caseId: CommonSchemas.DynamicString,
      recommendation: CommonSchemas.DynamicString,
      onApprove: CommonSchemas.Action,
      onReject: CommonSchemas.Action,
    }),
  },
  ({props}) => (
    <div className="card approval">
      <div>
        <div className="card-title">Sanction decision · case {props.caseId}</div>
        <div className="muted small">
          A click mints a one-time approval token in the BFF. The agent cannot press these buttons.
        </div>
      </div>
      <div className="buttons">
        <button className="approve" onClick={() => props.onApprove?.()}>
          Approve
        </button>
        <button className="reject" onClick={() => props.onReject?.()}>
          Reject
        </button>
      </div>
    </div>
  ),
);

export const lendingCatalog = new Catalog(LENDING_CATALOG_ID, '1.0', [
  Stack,
  Heading,
  AccessNotice,
  RecommendationBanner,
  CaseSummaryCard,
  ReasonCodeList,
  BureauSummary,
  DpdTrend,
  DocumentChecklist,
  CashflowSummary,
  CollectionsCard,
  ApprovalPanel,
] as any);
